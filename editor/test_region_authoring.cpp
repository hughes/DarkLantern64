#include "region_authoring.h"
#include <iostream>

using Json=nlohmann::json;
#define CHECK(value) do { if(!(value)){std::cerr<<__LINE__<<": "<<#value<<'\n';return 1;} } while(0)
static Json scene() {
    return {{"entities",{{{"id","spawn"},{"kind","spawn"}},{{"id","door"},{"kind","door"}},{{"id","loot"},{"kind","loot"}},{{"id","new-wall"},{"kind","static"}}}}};
}
static Json partition() {
    const Json west={{-4,-1,-2},{0,3,2}},east={{0,-1,-2},{4,3,2}};
    return {{"version",1},{"source","scene.json"},{"entry_region","west"},{"visibility","authored-sets"},
        {"regions",{
            {{"id","west"},{"bounds",west},{"activation_bounds",west},{"entities",{"spawn","door"}},{"neighbors",{"east"}},{"working_set",{"west","east"}}},
            {{"id","east"},{"bounds",east},{"activation_bounds",east},{"entities",{"loot"}},{"neighbors",{"west"}},{"working_set",{"east"}}}}},
        {"transitions",{
            {{"id","west-east"},{"from","west"},{"to","east"},{"bounds",{{-.1,-1,-.5},{.1,3,.5}}},{"door","door"}},
            {{"id","east-west"},{"from","east"},{"to","west"},{"bounds",{{-.1,-1,-.5},{.1,3,.5}}},{"door","door"}}}}};
}
static bool rejected(const Json& original,const std::string& op,const Json& args) {
    const auto snapshot=original.dump();
    try {DlRegionAuthoring::Apply(original,scene(),op,args);}catch(const std::exception&){return original.dump()==snapshot;}
    return false;
}
static int references_and_ownership() {
    const auto original=partition();
    auto renamed=DlRegionAuthoring::Apply(original,scene(),"rename_region",{{"id","west"},{"new_id","entry"}});
    CHECK(original.at("entry_region")=="west"&&renamed.at("entry_region")=="entry");
    CHECK(renamed.at("regions")[0].at("entities")==original.at("regions")[0].at("entities"));
    CHECK(renamed.at("regions")[0].at("working_set")==Json::array({"entry","east"}));
    CHECK(renamed.at("regions")[1].at("neighbors")==Json::array({"entry"}));
    CHECK(renamed.at("transitions")[0].at("from")=="entry"&&renamed.at("transitions")[1].at("to")=="entry");
    CHECK(rejected(original,"rename_region",{{"id","west"},{"new_id","east"}}));
    CHECK(rejected(original,"rename_region",{{"id","west"},{"new_id","9-invalid"}}));
    CHECK(rejected(original,"delete_region",{{"id","west"}}));
    CHECK(rejected(original,"delete_region",{{"id","west"},{"reassign_to","west"}}));
    auto removed=DlRegionAuthoring::Apply(renamed,scene(),"delete_region",{{"id","entry"},{"reassign_to","east"}});
    CHECK(removed.at("entry_region")=="east"&&removed.at("regions").size()==1&&removed.at("transitions").empty());
    CHECK(removed.at("regions")[0].at("working_set")==Json::array({"east"})&&removed.at("regions")[0].at("neighbors").empty());
    CHECK(removed.at("regions")[0].at("entities")==Json::array({"loot","spawn","door"}));
    CHECK(rejected(removed,"delete_region",{{"id","east"}}));
    return 0;
}
static int draft_creation_and_duplicate() {
    const auto original=partition();
    auto created=DlRegionAuthoring::Apply(original,scene(),"create_region",{{"id","annex"},{"bounds",{{4,-1,-2},{8,3,2}}}});
    CHECK(created.at("regions").size()==3&&created.at("regions")[2].at("entities").empty());
    CHECK(created.at("regions")[2].at("activation_bounds")==created.at("regions")[2].at("bounds"));
    CHECK(created.at("regions")[2].at("working_set")==Json::array({"annex"}));
    CHECK(rejected(original,"create_region",{{"id","bad"},{"bounds",{{4,-1,-2},{4,3,2}}}}));
    CHECK(rejected(original,"create_region",{{"id","bad"},{"bounds",{{4,-1,-2},{8,3,2}}},{"working_set",{"unknown"}}}));
    auto copy=DlRegionAuthoring::Apply(original,scene(),"duplicate_region",{{"id","west"},{"new_id","west-copy"},{"translation",{8,0,0}}});
    CHECK(copy.at("regions")[2].at("entities").empty()&&copy.at("regions")[2].at("neighbors").empty());
    CHECK(copy.at("regions")[2].at("bounds")[0][0]==4&&copy.at("regions")[2].at("activation_bounds")[1][0]==8);
    CHECK(copy.at("regions")[2].at("working_set")==Json::array({"west-copy","east"}));
    CHECK(copy.at("transitions")==original.at("transitions"));
    auto erased=DlRegionAuthoring::Apply(copy,scene(),"delete_region",{{"id","west-copy"}});
    CHECK(erased==original); // Empty metadata copies do not duplicate homes or topology.
    const auto homes=DlRegionAuthoring::Homes(copy,scene());CHECK(homes.at("unassigned")==Json::array({"new-wall"}));
    auto malformed=copy;malformed["regions"][2]["entities"]={"loot","missing"};
    const auto summary=DlRegionAuthoring::Homes(malformed,scene());
    CHECK(summary.at("multiply_owned").size()==1&&summary.at("missing_entities")==Json::array({"missing"}));
    return 0;
}
static int atomic_gate_pair() {
    auto original=partition();original["transitions"]=Json::array();for(auto& row:original["regions"])row["neighbors"]=Json::array();
    const Json args={{"id","opening-out"},{"reverse_id","opening-back"},{"from","west"},{"to","east"},{"bounds",{{-.1,-1,-.5},{.1,3,.5}}},{"door","door"}};
    auto paired=DlRegionAuthoring::Apply(original,scene(),"add_transition_pair",args);
    CHECK(paired.at("transitions").size()==2&&paired.at("transitions")[1].at("from")=="east");
    CHECK(paired.at("regions")[0].at("neighbors")==Json::array({"east"})&&paired.at("regions")[1].at("neighbors")==Json::array({"west"}));
    CHECK(paired.at("regions")[0].at("working_set")==original.at("regions")[0].at("working_set"));
    CHECK(paired.at("regions")[1].at("working_set")==Json::array({"east"})); // Independent visibility is preserved.
    auto explicitSets=args;explicitSets["from_working_set"]={"west"};explicitSets["to_working_set"]={"east","west"};
    paired=DlRegionAuthoring::Apply(original,scene(),"add_transition_pair",explicitSets);
    CHECK(paired.at("regions")[0].at("working_set")==Json::array({"west"})&&paired.at("regions")[1].at("working_set")==Json::array({"east","west"}));
    for(const auto& change:Json::array({Json{{"reverse_id","opening-out"}},Json{{"reverse_bounds",{{3,-1,-.5},{4,3,.5}}}},Json{{"door","loot"}},Json{{"to_working_set",{"west"}}}})) {
        auto invalid=args;invalid.update(change);CHECK(rejected(original,"add_transition_pair",invalid));
    }
    CHECK(rejected(partition(),"add_transition_pair",args)); // Same door/from is already assigned.
    return 0;
}
static int reassignment_preserves_visible_geometry() {
    auto original=partition();
    original["regions"].push_back({{"id","annex"},{"bounds",{{0,-1,-2},{8,3,2}}},{"activation_bounds",{{4,-1,-2},{8,3,2}}},
        {"entities",Json::array()},{"neighbors",Json::array()},{"working_set",{"annex","east"}}});
    const auto removed=DlRegionAuthoring::Apply(original,scene(),"delete_region",{{"id","east"},{"reassign_to","annex"}});
    CHECK(removed.at("regions").size()==2&&removed.at("entry_region")=="west");
    CHECK(removed.at("regions")[0].at("working_set")==Json::array({"west","annex"}));
    CHECK(removed.at("regions")[1].at("working_set")==Json::array({"annex"}));
    CHECK(removed.at("regions")[1].at("entities")==Json::array({"loot"}));
    CHECK(removed.at("regions")[0].at("neighbors").empty()&&removed.at("regions")[1].at("neighbors").empty());
    CHECK(removed.at("transitions").empty());
    CHECK(original.at("regions")[0].at("working_set")==Json::array({"west","east"}));
    return 0;
}
int main() {
    CHECK(references_and_ownership()==0);CHECK(draft_creation_and_duplicate()==0);CHECK(atomic_gate_pair()==0);
    CHECK(reassignment_preserves_visible_geometry()==0);
    std::cout<<"Region authoring: atomic CRUD, explicit ownership reassignment, reference repair, independent paired gates and unassigned summaries passed\n";
}
