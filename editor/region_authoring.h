#pragma once
#include <nlohmann/json.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

// Pure draft mutations. A caller publishes the returned partition together
// with its scene in one history/save transaction only after this succeeds.
// Empty or disconnected draft regions remain editable; the content cooker
// separately checks coverage, reachability and target working-set capacity.
class DlRegionAuthoring {
public:
    using Json = nlohmann::json;
    static bool Supports(const std::string& op) {
        return op=="create_region"||op=="rename_region"||op=="duplicate_region"||
            op=="delete_region"||op=="add_transition_pair";
    }
    static Json Apply(const Json& partition,const Json& scene,const std::string& op,const Json& args) {
        if(!args.is_object())throw std::runtime_error("Region command arguments must be an object");
        Json candidate=partition;CheckRegions(candidate);
        auto& regions=candidate.at("regions");
        const bool authored=candidate.value("visibility",std::string())=="authored-sets";
        if(op=="create_region") {
            const auto id=Id(args.at("id"),"Region ID");RequireUnused(candidate,id);
            if(regions.size()>=256)throw std::runtime_error("A partition supports at most 256 manual regions");
            CheckBounds(args.at("bounds"),"Region bounds");
            Json row={{"id",id},{"bounds",args.at("bounds")},{"entities",Json::array()},{"neighbors",Json::array()}};
            if(authored||args.contains("activation_bounds")) {
                row["activation_bounds"]=args.value("activation_bounds",args.at("bounds"));CheckBounds(row.at("activation_bounds"),"Activation bounds");
            }
            if(authored||args.contains("working_set"))row["working_set"]=args.value("working_set",Json::array({id}));
            regions.push_back(std::move(row));
            if(Region(candidate,id).contains("working_set"))CheckWorkingSet(candidate,id,Region(candidate,id).at("working_set"));
        } else if(op=="rename_region") {
            const auto id=Id(args.at("id"),"Region ID"),renamed=Id(args.at("new_id"),"New region ID");
            Region(candidate,id);if(id==renamed)return candidate;RequireUnused(candidate,renamed);
            Region(candidate,id)["id"]=renamed;
            if(candidate.at("entry_region")==id)candidate["entry_region"]=renamed;
            for(auto& row:regions)for(const char* field:{"neighbors","working_set"})if(row.contains(field))ReplaceReference(row[field],id,renamed);
            if(candidate.contains("transitions"))for(auto& gate:Gates(candidate))for(const char* field:{"from","to"})if(gate.at(field)==id)gate[field]=renamed;
        } else if(op=="duplicate_region") {
            const auto id=Id(args.at("id"),"Region ID"),copied=Id(args.at("new_id"),"New region ID");RequireUnused(candidate,copied);
            if(regions.size()>=256)throw std::runtime_error("A partition supports at most 256 manual regions");
            Json row=Region(candidate,id);row["id"]=copied;row["entities"]=Json::array();row["neighbors"]=Json::array();
            const auto delta=args.value("translation",Json::array({0,0,0}));CheckVector(delta,"Region translation");
            for(const char* field:{"bounds","activation_bounds"})if(row.contains(field)) {
                CheckBounds(row.at(field),field);
                for(auto& point:row[field])for(unsigned axis=0;axis<3;++axis)point[axis]=point.at(axis).get<double>()+delta.at(axis).get<double>();
                CheckBounds(row.at(field),field);
            }
            if(row.contains("working_set"))ReplaceReference(row["working_set"],id,copied);
            regions.push_back(std::move(row));
            if(Region(candidate,copied).contains("working_set"))CheckWorkingSet(candidate,copied,Region(candidate,copied).at("working_set"));
        } else if(op=="delete_region") {
            const auto id=Id(args.at("id"),"Region ID");const Json removed=Region(candidate,id);
            if(regions.size()==1)throw std::runtime_error("A partition must retain at least one region");
            std::string destination;
            if(args.contains("reassign_to")) {
                destination=Id(args.at("reassign_to"),"Reassignment region");
                if(destination==id)throw std::runtime_error("Reassign owned objects to a different region");
                Region(candidate,destination);
            }
            if(!removed.at("entities").empty()&&destination.empty())throw std::runtime_error("Deleting an owned region requires explicit reassign_to");
            if(!destination.empty())for(const auto& entity:removed.at("entities")) {
                auto& homes=Region(candidate,destination)["entities"];
                if(std::find(homes.begin(),homes.end(),entity)==homes.end())homes.push_back(entity);
            }
            regions.erase(std::remove_if(regions.begin(),regions.end(),[&](const Json& row){return row.at("id")==id;}),regions.end());
            if(candidate.at("entry_region")==id) {
                if(!destination.empty())candidate["entry_region"]=destination;
                else {std::string first=regions.front().at("id");for(const auto& row:regions)first=std::min(first,row.at("id").get<std::string>());candidate["entry_region"]=first;}
            }
            for(auto& row:regions) {
                if(row.contains("neighbors"))RemoveReference(row["neighbors"],id);
                if(row.contains("working_set")) {
                    if(destination.empty())RemoveReference(row["working_set"],id);
                    else {
                        // A visible owned region's geometry moves into the
                        // reassignment target. Preserve that visibility without
                        // adding duplicate target IDs to the authored set.
                        ReplaceReference(row["working_set"],id,destination);
                        bool seen=false;auto& visible=row["working_set"];
                        visible.erase(std::remove_if(visible.begin(),visible.end(),[&](const Json& peer){
                            if(peer!=destination)return false;
                            if(seen)return true;
                            seen=true;return false;
                        }),visible.end());
                    }
                }
            }
            if(candidate.contains("transitions")) {
                auto& gates=Gates(candidate);gates.erase(std::remove_if(gates.begin(),gates.end(),[&](const Json& gate){return gate.at("from")==id||gate.at("to")==id;}),gates.end());
            }
            if(authored)SyncOutgoingNeighbors(candidate);
        } else if(op=="add_transition_pair") {
            if(!authored)throw std::runtime_error("Paired gates require authored-sets visibility");
            const auto from=Id(args.at("from"),"Source region"),to=Id(args.at("to"),"Target region");
            Region(candidate,from);Region(candidate,to);
            if(from==to)throw std::runtime_error("A gate must join two different regions");
            const auto forward=Id(args.at("id"),"Gate ID"),reverse=Id(args.at("reverse_id"),"Reverse gate ID");
            if(forward==reverse)throw std::runtime_error("Paired gates need distinct IDs");
            Json gate={{"id",forward},{"from",from},{"to",to},{"bounds",args.at("bounds")}};
            Json back={{"id",reverse},{"from",to},{"to",from},{"bounds",args.value("reverse_bounds",args.at("bounds"))}};
            if(args.contains("door")&&!args.at("door").is_null()){gate["door"]=args.at("door");back["door"]=args.at("door");}
            if(!candidate.contains("transitions"))candidate["transitions"]=Json::array();
            auto& gates=Gates(candidate);gates.push_back(gate);gates.push_back(back);
            if(args.contains("from_working_set")){CheckWorkingSet(candidate,from,args.at("from_working_set"));Region(candidate,from)["working_set"]=args.at("from_working_set");}
            if(args.contains("to_working_set")){CheckWorkingSet(candidate,to,args.at("to_working_set"));Region(candidate,to)["working_set"]=args.at("to_working_set");}
            CheckGates(candidate,scene);SyncOutgoingNeighbors(candidate);
        } else throw std::runtime_error("Unknown region mutation");
        CheckRegions(candidate);return candidate;
    }
    static Json Homes(const Json& partition,const Json& scene) {
        std::set<std::string> known;std::map<std::string,std::vector<std::string>> owners;
        if(scene.is_object()&&scene.contains("entities")&&scene.at("entities").is_array())for(const auto& entity:scene.at("entities"))
            if(entity.is_object()&&entity.contains("id")&&entity.at("id").is_string())known.insert(entity.at("id").get<std::string>());
        Json regions=Json::array();
        if(partition.is_object()&&partition.contains("regions")&&partition.at("regions").is_array())for(const auto& row:partition.at("regions")) {
            if(!row.is_object()||!row.contains("id")||!row.at("id").is_string())continue;
            const auto id=row.at("id").get<std::string>();size_t count=0;
            if(row.contains("entities")&&row.at("entities").is_array())for(const auto& entity:row.at("entities"))if(entity.is_string()){owners[entity.get<std::string>()].push_back(id);++count;}
            regions.push_back({{"id",id},{"home_count",count}});
        }
        Json unassigned=Json::array(),missing=Json::array(),multiple=Json::array();
        for(const auto& id:known)if(!owners.contains(id))unassigned.push_back(id);
        for(const auto& [id,homes]:owners){if(!known.contains(id))missing.push_back(id);if(homes.size()>1)multiple.push_back({{"id",id},{"regions",homes}});}
        return {{"regions",regions},{"unassigned",unassigned},{"missing_entities",missing},{"multiply_owned",multiple}};
    }
    static void SyncOutgoingNeighbors(Json& partition) {
        std::map<std::string,std::set<std::string>> neighbors;
        if(partition.contains("transitions"))for(const auto& gate:Gates(partition))neighbors[gate.at("from").get<std::string>()].insert(gate.at("to").get<std::string>());
        for(auto& row:partition.at("regions"))row["neighbors"]=neighbors[row.at("id").get<std::string>()];
    }
    static void CheckGate(const Json& partition,const Json& scene,const Json& gate) {
        Id(gate.at("id"),"Gate ID");const auto from=Id(gate.at("from"),"Source region"),to=Id(gate.at("to"),"Target region");
        if(from==to)throw std::runtime_error("A gate must join two different regions");
        CheckBounds(gate.at("bounds"),"Gate bounds");
        for(const auto& id:{from,to}) {
            const auto& region=Region(partition,id);const auto& activation=region.at("activation_bounds");CheckBounds(activation,"Activation bounds");
            for(unsigned axis=0;axis<3;++axis)if(std::max(gate.at("bounds")[0][axis].get<double>(),activation[0][axis].get<double>())>
                std::min(gate.at("bounds")[1][axis].get<double>(),activation[1][axis].get<double>()))throw std::runtime_error("A gate must intersect both activation regions");
        }
        if(gate.contains("door")) {
            const auto door=Id(gate.at("door"),"Gate door ID");bool found=false,home=false;
            for(const auto& entity:scene.at("entities"))if(entity.at("id")==door){found=entity.at("kind")=="door";break;}
            for(const auto& id:{from,to})for(const auto& entity:Region(partition,id).at("entities"))if(entity==door)home=true;
            if(!found||!home)throw std::runtime_error("A gate door must identify an existing door owned by an endpoint region");
        }
    }
    static void CheckGates(const Json& partition,const Json& scene) {
        if(!partition.contains("transitions")||!partition.at("transitions").is_array())throw std::runtime_error("Transitions must be an array");
        const auto& gates=partition.at("transitions");if(gates.size()>256)throw std::runtime_error("A partition supports at most 256 directed gates");
        std::set<std::string> ids,doors;std::map<std::string,unsigned> outgoing;
        for(const auto& gate:gates) {
            CheckGate(partition,scene,gate);const auto id=gate.at("id").get<std::string>(),from=gate.at("from").get<std::string>();
            if(!ids.insert(id).second)throw std::runtime_error("Gate ID already exists: "+id);
            if(++outgoing[from]>8)throw std::runtime_error("A region supports at most eight outgoing gates");
            if(gate.contains("door")&&!doors.insert(from+"/"+gate.at("door").get<std::string>()).second)throw std::runtime_error("A door can select only one gate target from a region");
        }
    }
private:
    static std::string Id(const Json& value,const char* label) {
        if(!value.is_string())throw std::runtime_error(std::string(label)+" must be a stable ASCII identifier");
        const auto id=value.get<std::string>();const auto alpha=[](char c){return (c>='A'&&c<='Z')||(c>='a'&&c<='z');};
        if(id.empty()||id.size()>64||!alpha(id.front())||!std::all_of(id.begin()+1,id.end(),[&](char c){return alpha(c)||(c>='0'&&c<='9')||c=='_'||c=='-';}))
            throw std::runtime_error(std::string(label)+" must be 1..64 ASCII characters, starting with a letter");
        return id;
    }
    static void CheckVector(const Json& value,const char* label) {
        if(!value.is_array()||value.size()!=3)throw std::runtime_error(std::string(label)+" needs XYZ");
        for(const auto& number:value)if(!number.is_number()||!std::isfinite(number.get<double>())||std::abs(number.get<double>())>1024)
            throw std::runtime_error(std::string(label)+" needs finite coordinates within -1024..1024");
    }
    static void CheckBounds(const Json& value,const char* label) {
        if(!value.is_array()||value.size()!=2)throw std::runtime_error(std::string(label)+" needs minimum and maximum XYZ");
        CheckVector(value.at(0),label);CheckVector(value.at(1),label);
        for(unsigned axis=0;axis<3;++axis)if(value[0][axis].get<double>()>=value[1][axis].get<double>())throw std::runtime_error(std::string(label)+" minimum must be below maximum");
    }
    static Json& Region(Json& partition,const std::string& id) {
        for(auto& row:partition.at("regions"))if(row.at("id")==id)return row;
        throw std::runtime_error("Unknown region: "+id);
    }
    static const Json& Region(const Json& partition,const std::string& id) {
        for(const auto& row:partition.at("regions"))if(row.at("id")==id)return row;
        throw std::runtime_error("Unknown region: "+id);
    }
    static void RequireUnused(const Json& partition,const std::string& id) {
        for(const auto& row:partition.at("regions"))if(row.at("id")==id)throw std::runtime_error("Region ID already exists: "+id);
    }
    static void ReplaceReference(Json& list,const std::string& old,const std::string& replacement) {
        if(!list.is_array())throw std::runtime_error("Region references must be arrays");
        for(auto& id:list){if(!id.is_string())throw std::runtime_error("Region references must be IDs");if(id==old)id=replacement;}
    }
    static void RemoveReference(Json& list,const std::string& removed) {
        if(!list.is_array())throw std::runtime_error("Region references must be arrays");
        list.erase(std::remove_if(list.begin(),list.end(),[&](const Json& id){return id==removed;}),list.end());
    }
    static Json& Gates(Json& partition) {
        auto& gates=partition.at("transitions");if(!gates.is_array())throw std::runtime_error("Transitions must be an array");
        for(const auto& gate:gates)if(!gate.is_object()||!gate.contains("id")||!gate.contains("from")||!gate.contains("to"))throw std::runtime_error("Repair malformed transitions before changing regions");
        return gates;
    }
    static void CheckWorkingSet(const Json& partition,const std::string& self,const Json& list) {
        if(!list.is_array()||list.empty())throw std::runtime_error("A working set needs region IDs including self");
        std::set<std::string> seen;for(const auto& value:list){const auto id=Id(value,"Working-set region");Region(partition,id);if(!seen.insert(id).second)throw std::runtime_error("Working-set IDs must be distinct");}
        if(!seen.contains(self))throw std::runtime_error("Working set must include its own region");
    }
    static void CheckRegions(const Json& partition) {
        if(!partition.is_object()||!partition.contains("regions")||!partition.at("regions").is_array()||partition.at("regions").empty()||partition.at("regions").size()>256)
            throw std::runtime_error("A partition needs 1..256 region rows");
        std::set<std::string> ids;
        for(const auto& region:partition.at("regions")) {
            const auto id=Id(region.at("id"),"Region ID");if(!ids.insert(id).second)throw std::runtime_error("Region IDs must be distinct");
            for(const char* field:{"entities","neighbors"}) {
                if(!region.at(field).is_array())throw std::runtime_error(std::string(field)+" must be an ID array");
                for(const auto& value:region.at(field))Id(value,field);
            }
        }
        Region(partition,Id(partition.at("entry_region"),"Entry region"));
    }
};
