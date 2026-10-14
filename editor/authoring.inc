    DocumentHistory<Json> history_;
    Json streamDraft_=nullptr;
    std::string streamSnapshot_,streamSavedSha_,sourceSavedSha_,jobStreamSnapshot_;
    fs::path streamPath_;
    std::vector<std::pair<std::string,std::optional<EntityId>>> editorCommands_;
    std::set<std::string> selection_;
    std::string lastPreviewDraft_;
    struct FloorVertexInput {Json source;float points[3][3]{};bool pending=false;};
    std::map<std::string,FloorVertexInput> floorVertexInputs_;
    float snapMetres_=.25f, groupOffset_[3]={0,0,0}, blockSize_[3]={2,.2f,2};
    float groupYaw_=90,groupScale_=1;
    char groupName_[65]="group";
    char prefabName_[65]="encounter";
    Json AuthorSnapshot() const{return {{"scene",document_},{"partition",streamDraft_}};}
    static void PruneRegionHomes(Json& partition,const std::set<std::string>& ids) {
        if(!partition.is_object()||!partition.contains("regions")||!partition.at("regions").is_array())return;
        for(auto& region:partition["regions"])if(region.is_object()&&region.contains("entities")&&region.at("entities").is_array()) {
            auto& homes=region["entities"];
            homes.erase(std::remove_if(homes.begin(),homes.end(),[&](const Json& id){return id.is_string()&&ids.contains(id.get<std::string>());}),homes.end());
        }
        if(partition.contains("transitions")&&partition.at("transitions").is_array())
            for(auto& gate:partition["transitions"])if(gate.is_object()&&gate.contains("door")&&gate.at("door").is_string()&&ids.contains(gate.at("door").get<std::string>()))gate.erase("door");
    }
    void UpdateAuthorDirty(){dirty_=document_!=Json::parse(sourceSnapshot_)||
        (!streamDraft_.is_null()&&(streamSnapshot_.empty()||streamDraft_!=Json::parse(streamSnapshot_)));}
    void ObserveDocument(){if(!engine_.IsPlaying())history_.Commit(AuthorSnapshot());}
    void StagePartition(std::vector<std::wstring>& args,const fs::path& stage) const {
        if(streamDraft_.is_null())return;
        AtomicWrite(stage,streamDraft_.dump(2)+"\n");
        args.insert(args.end(),{L"--staged-mission",stage.wstring(),L"--expected-mission-sha256",fs::path(streamSavedSha_).wstring()});
    }
    void RefreshDraft() {
        if(engine_.IsPlaying()||Busy())throw std::runtime_error("Finish Play/build before refreshing authoring");
        const auto snapshot=document_.dump(2)+"\n";
        const auto stage=queue_/"preview-snapshot.json";AtomicWrite(stage,snapshot);
        std::vector<std::wstring> args{L"cook",L"--level",source_.wstring(),L"--staged",stage.wstring()};
        StagePartition(args,queue_/"preview-partition.json");
        auto cooked=ProjectCommand(args);
        ApplyPreparedPreview(cooked);
    }
    void RestoreHistory(bool redo) {
        ObserveDocument();const auto restored=redo?history_.Redo():history_.Undo();
        document_=restored.at("scene");streamDraft_=restored.at("partition");
        UpdateAuthorDirty();selection_.clear();
        selected_=document_.at("entities").empty()?"":document_["entities"][0]["id"].get<std::string>();
        structureDirty_=true;lastPreviewDraft_.clear();
    }
    std::set<std::string> SelectedIds(const Json& args) const {
        auto ids=selection_;
        if(args.contains("ids")){ids.clear();for(const auto& id:args.at("ids"))ids.insert(id.get<std::string>());}
        if(ids.empty()&&!selected_.empty())ids.insert(selected_);
        if(ids.empty())throw std::runtime_error("Select at least one object");
        for(const auto& id:ids){bool found=false;for(const auto& row:document_.at("entities"))if(row.at("id")==id)found=true;
            if(!found)throw std::runtime_error("Unknown selection: "+id);}
        return ids;
    }
    Json AuthorCommand(const std::string& op,const Json& args) {
        if(engine_.IsPlaying())throw std::runtime_error("Stop Play before changing authoring");
        ObserveDocument();
        if(op=="undo"||op=="redo"){RestoreHistory(op=="redo");return {{"dirty",dirty_}};}
        if(op=="refresh_preview"){RefreshDraft();return {{"refreshed",true}};}
        if(op=="select_entities"){selection_=SelectedIds(args);selected_=*selection_.begin();return {{"ids",selection_}};}
        Json candidate=document_,partition=streamDraft_;Json created=Json::array();
        if(op=="save_prefab") {
            const auto ids=SelectedIds(args);const std::string name=args.value("name",std::string("encounter"));
            if(name.empty()||name.size()>64)throw std::runtime_error("Prefab name must be 1..64 characters");
            Json rows=Json::array();for(const auto& row:candidate["entities"])if(ids.contains(row.at("id"))) {
                if(row.at("kind")=="spawn")throw std::runtime_error("Player spawn cannot belong to an encounter prefab");rows.push_back(row);
            }
            const auto origin=rows[0].at("transform").at("position");
            for(auto& row:rows)for(int i=0;i<3;++i)row["transform"]["position"][i]=row["transform"]["position"][i].get<float>()-origin[i].get<float>();
            candidate["encounter_prefabs"][name]={{"entities",rows}};
        } else if(op=="place_prefab") {
            const std::string name=args.at("name");const auto& prefab=candidate.at("encounter_prefabs").at(name);
            const auto position=args.value("position",Json::array({0,0,0}));CheckVector(position,"position",-1024,1024);
            auto used=UsedIds();std::unordered_map<std::string,std::string> remap;
            for(const auto& row:prefab.at("entities"))remap[row.at("id")]=NewId(Json::object(),"id",name+"-"+row.at("id").get<std::string>(),used);
            for(auto row:prefab.at("entities")) {
                const std::string templateId=row.at("id");row["id"]=remap.at(templateId);
                row["prefab_origin"]={{"name",name},{"object",templateId}};
                for(int i=0;i<3;++i)row["transform"]["position"][i]=row["transform"]["position"][i].get<float>()+position[i].get<float>();
                if(row.contains("target")&&remap.contains(row.at("target")))row["target"]=remap.at(row.at("target"));
                for(const char* field:{"targets","patrol"})if(row.contains(field))for(auto& ref:row[field])if(remap.contains(ref))ref=remap.at(ref);
                created.push_back(row.at("id"));candidate["entities"].push_back(row);
            }
        } else if(op=="set_game_object") {
            const std::string id=args.at("id");const auto& patch=args.at("patch");
            if(!patch.is_object())throw std::runtime_error("Object patch must be an object");
            auto found=std::find_if(candidate["entities"].begin(),candidate["entities"].end(),[&](const Json& row){return row.at("id")==id;});
            if(found==candidate["entities"].end())throw std::runtime_error("Unknown object");
            for(const auto& [key,value]:patch.items()) {
                if(key=="targets") {
                    if(found->at("kind")!="control"||!value.is_array()||value.size()>64)throw std::runtime_error("Controls accept up to 64 door targets");
                    for(const auto& ref:value){const auto* target=Entity(ref.get<std::string>());if(!target||target->at("kind")!="door")throw std::runtime_error("Target must identify a door");}
                    found->erase("target");
                } else if(key=="kind") {
                    if((found->at("kind")!="static"&&found->at("kind")!="loot")||(value!="static"&&value!="loot"))throw std::runtime_error("Only decorative prop/collectible conversion is supported");
                    if(value=="loot")found->erase("collider");
                } else if(key=="value"||key=="required_loot") {
                    if(!value.is_number_integer()||value<0||value>1000000)throw std::runtime_error("Loot values must be 0..1000000");
                } else if(key=="interaction_radius") {
                    if(!value.is_number()||!std::isfinite(value.get<float>())||value<.05||value>128)throw std::runtime_error("Interaction radius must be .05..128");
                } else if(key=="zone") {
                    if(found->at("kind")!="objective"||!value.is_boolean())throw std::runtime_error("Only objectives can be zones");
                } else if(key=="model"||key=="material") {
                    const auto definitions=ResolvedDefinitions(key=="model"?"assets":"materials");
                    if(!value.is_string()||!std::any_of(definitions.begin(),definitions.end(),[&](const Json& row){return row.at("id")==value;}))throw std::runtime_error("Unknown asset definition");
                } else if(key=="collider") {
                    if(found->at("kind")!="static"&&found->at("kind")!="door")throw std::runtime_error("Only static objects and doors have collision proxies");
                    if(value.at("shape")!="box")throw std::runtime_error("Only box collision is supported");
                    CheckVector(value.at("center"),"collider centre",-1024,1024);CheckVector(value.at("half_size"),"collider half size",.001,1024);
                } else if(key=="floor") {
                    if(found->at("kind")!="static"||found->contains("collider"))throw std::runtime_error("A floor plane needs a static object without an opaque box collider");
                    if(!value.is_object()||!value.contains("vertices")||!value.at("vertices").is_array()||value.at("vertices").size()!=3)
                        throw std::runtime_error("A floor needs three local XYZ vertices");
                    for(const auto& point:value.at("vertices"))CheckVector(point,"floor vertex",-1024,1024);
                } else throw std::runtime_error("Unsupported object property");
                (*found)[key]=value;
            }
        } else if(op=="add_floor") {
            auto used=UsedIds();const auto id=NewId(args,"id","floor-triangle",used);
            const auto position=args.value("position",PlacementPosition(args));CheckVector(position,"position",-1024,1024);
            const auto vertices=args.value("vertices",Json::array({{-1,0,-1},{1,0,-1},{-1,0,1}}));
            if(!vertices.is_array()||vertices.size()!=3)throw std::runtime_error("A floor needs three local XYZ vertices");
            for(const auto& point:vertices)CheckVector(point,"floor vertex",-1024,1024);
            candidate["entities"].push_back({{"id",id},{"kind","static"},{"floor",{{"vertices",vertices}}},
                {"transform",{{"position",position},{"rotation",{0,0,0}},{"scale",{1,1,1}}}}});created.push_back(id);
        } else if(op=="add_blockout"||op=="add_game_object") {
            const auto position=args.value("position",Json::array({0,0,0}));CheckVector(position,"position",-1024,1024);
            const auto size=args.value("size",op=="add_game_object"?Json::array({.3,.3,.3}):Json::array({2,.2,2}));CheckVector(size,"size",.01f,128);
            const auto shape=args.value("shape",std::string("box"));
            auto used=UsedIds();const auto base=NewId(args,"id",shape,used);
            if(!std::any_of(candidate["assets"].begin(),candidate["assets"].end(),[](const Json& row){return row.at("id")=="mesh-blockout-box";}))
                candidate["assets"].push_back({{"id","mesh-blockout-box"},{"type","primitive"},{"shape","box"}});
            if(!std::any_of(candidate["materials"].begin(),candidate["materials"].end(),[](const Json& row){return row.at("id")=="mat-blockout";}))
                candidate["materials"].push_back({{"id","mat-blockout"},{"color",{.5,.55,.6,1}}});
            const auto add=[&](const std::string& id,Json p,Json dimensions) {
                candidate["entities"].push_back({{"id",id},{"kind","static"},{"model","mesh-blockout-box"},{"material","mat-blockout"},
                    {"transform",{{"position",p},{"rotation",{0,0,0}},{"scale",dimensions}}},
                    {"collider",{{"shape","box"},{"center",{0,0,0}},{"half_size",{.5,.5,.5}}}}});created.push_back(id);
            };
            if(shape=="box"||shape=="floor"||shape=="wall"||shape=="platform")add(base,position,size);
            else if(shape=="stairs") {
                const int count=args.value("steps",8);if(count<1||count>32)throw std::runtime_error("Stairs need 1..32 steps");
                const float rise=size[1].get<float>()/count,run=size[2].get<float>()/count;
                if(rise>.28f)throw std::runtime_error("Stair rise exceeds the shared controller's 0.28m step height");
                for(int i=0;i<count;++i){auto p=position;float height=rise*(i+1);p[1]=p[1].get<float>()+height*.5f;
                    p[2]=p[2].get<float>()+(i+.5f)*run;auto id=NewId(Json::object(),"id",base+"-step",used);
                    add(id,p,Json::array({size[0],height,run}));}
            } else if(shape=="doorway") {
                const float width=size[0],height=size[1],depth=size[2];
                if(width<1.4f||height<2.2f)throw std::runtime_error("Doorway needs at least 1.4m width and 2.2m height");
                const float post=.2f;
                for(int side:{-1,1}){auto p=position;p[0]=p[0].get<float>()+side*(width-post)*.5f;p[1]=p[1].get<float>()+height*.5f;
                    add(NewId(Json::object(),"id",base+"-post",used),p,Json::array({post,height,depth}));}
                auto p=position;p[1]=p[1].get<float>()+height-post*.5f;
                add(NewId(Json::object(),"id",base+"-lintel",used),p,Json::array({width-2*post,post,depth}));
            } else throw std::runtime_error("Unknown blockout shape");
            if(op=="add_game_object") {
                const std::string kind=args.at("kind");auto& object=candidate["entities"].back();
                if(kind!="door"&&kind!="control"&&kind!="loot"&&kind!="objective"&&kind!="light")throw std::runtime_error("Unsupported gameplay object kind");
                object["kind"]=kind;
                if(kind!="door")object.erase("collider");
                if(kind=="control") {
                    Json targets=args.value("targets",Json::array());
                    if(targets.empty())for(const auto& row:candidate["entities"])if(row.at("kind")=="door"){targets.push_back(row.at("id"));break;}
                    if(targets.empty())throw std::runtime_error("Place a door before its control");
                    object["targets"]=targets;
                }
                if(kind=="loot"){object["value"]=args.value("value",1);object["loot_highlight"]=true;}
                if(kind=="objective"){object["required_loot"]=args.value("required_loot",0);object["zone"]=args.value("zone",false);}
                if(kind=="light") {
                    object.erase("model");object.erase("material");object["radius"]=8;object["intensity"]=.5;object["color"]={1,.75,.4};
                }
            }
        } else {
            const auto ids=SelectedIds(args);
            if(op=="transform_selection") {
                const auto delta=args.value("translation",Json::array({0,0,0}));CheckVector(delta,"translation",-1024,1024);
                const float snap=args.value("snap",0.f);
                if(!std::isfinite(snap)||snap<0||snap>128)throw std::runtime_error("Invalid snap spacing");
                glm::vec3 pivot(0);for(const auto& row:candidate["entities"])if(ids.contains(row.at("id")))pivot+=Vector(row.at("transform").at("position"));
                pivot/=float(ids.size());if(args.contains("pivot")){CheckVector(args.at("pivot"),"pivot",-1024,1024);pivot=Vector(args.at("pivot"));}
                for(auto& row:candidate["entities"])if(ids.contains(row.at("id"))) {
                    auto t=TransformAboutPivot(PreviewTransform(row.at("transform")),pivot,Vector(delta),args.value("yaw",0.f),args.value("scale",1.f));
                    if(snap>0)t.position=SnapPosition(t.position,snap);
                    auto degrees=glm::degrees(glm::eulerAngles(t.rotation));
                    if(row.contains("collider")){degrees={0,row.at("transform").at("rotation")[1].get<float>()+args.value("yaw",0.f),0};}
                    Json patch={{"position",{t.position.x,t.position.y,t.position.z}},{"rotation",{degrees.x,degrees.y,degrees.z}},{"scale",{t.scale.x,t.scale.y,t.scale.z}}};
                    PatchTransform(row["transform"],patch);
                }
            } else if(op=="drop_selection") {
                for(auto& row:candidate["entities"])if(ids.contains(row.at("id"))) {
                    auto& p=row["transform"]["position"];float offset=0;
                    if(row.contains("collider"))offset=(row["collider"]["center"][1].get<float>()-row["collider"]["half_size"][1].get<float>())*row["transform"]["scale"][1].get<float>();
                    float floor=-INFINITY;
                    for(const auto& support:candidate["entities"])if(!ids.contains(support.at("id"))&&(support.contains("collider")||support.contains("floor"))) {
                        const auto t=PreviewTransform(support.at("transform"));
                        if(support.contains("floor")) {
                            const auto& vertices=support.at("floor").at("vertices");glm::vec3 world[3];
                            for(int i=0;i<3;++i)world[i]=t.position+t.rotation*(Vector(vertices.at(i))*t.scale);
                            const auto a=world[0],b=world[1]-a,c=world[2]-a;const float determinant=b.x*c.z-b.z*c.x;
                            if(std::isfinite(determinant)&&std::abs(determinant)>1e-8f) {
                                const auto delta=Vector(p)-a;
                                const float u=(delta.x*c.z-delta.z*c.x)/determinant,v=(b.x*delta.z-b.z*delta.x)/determinant;
                                const float top=a.y+u*b.y+v*c.y;
                                if(u>=-1e-6f&&v>=-1e-6f&&u+v<=1.000001f&&std::isfinite(top)&&top<=p[1].get<float>()+offset+.01f)
                                    floor=std::max(floor,top);
                            }
                            continue;
                        }
                        const glm::vec3 centre=t.position+t.rotation*(Vector(support["collider"]["center"])*t.scale);
                        const glm::vec3 half=Vector(support["collider"]["half_size"])*t.scale;
                        const glm::vec3 local=glm::inverse(t.rotation)*(Vector(p)-centre);
                        float top=centre.y+half.y;
                        if(std::abs(local.x)<=half.x&&std::abs(local.z)<=half.z&&top<=p[1].get<float>()+offset+.01f)floor=std::max(floor,top);
                    }
                    if(!std::isfinite(floor))throw std::runtime_error("No supporting collision surface below "+row.at("id").get<std::string>());
                    p[1]=floor-offset;
                }
            } else if(op=="group_entities") {
                const auto name=args.value("name",std::string("group"));
                if(name.empty()||name.size()>64)throw std::runtime_error("Group name must be 1..64 characters");
                candidate["groups"][name]=ids;
            } else if(op=="duplicate_entities") {
                std::unordered_map<std::string,std::string> remap;auto used=UsedIds();
                for(const auto& id:ids)remap[id]=NewId(Json::object(),"id",id+"-copy",used);
                const auto offset=args.value("translation",Json::array({1,0,0}));CheckVector(offset,"translation",-1024,1024);
                Json copies=Json::array();
                for(const auto& row:candidate["entities"])if(ids.contains(row.at("id"))) {
                    if(row.at("kind")=="spawn")throw std::runtime_error("A level has one player spawn");
                    Json copy=row;copy["id"]=remap.at(row.at("id"));
                    for(int i=0;i<3;++i)copy["transform"]["position"][i]=row.at("transform").at("position")[i].get<float>()+offset[i].get<float>();
                    if(copy.contains("target")&&remap.contains(copy.at("target")))copy["target"]=remap.at(copy.at("target"));
                    for(const char* field:{"patrol","targets"})if(copy.contains(field))for(auto& ref:copy[field])if(remap.contains(ref))ref=remap.at(ref);
                    copies.push_back(copy);created.push_back(copy.at("id"));
                }
                for(auto& copy:copies)candidate["entities"].push_back(copy);
            } else if(op=="delete_entities") {
                for(const auto& row:candidate["entities"]) {
                    if(ids.contains(row.at("id"))){if(row.at("kind")=="spawn")throw std::runtime_error("Cannot delete the player spawn");continue;}
                    if(row.contains("target")&&ids.contains(row.at("target")))throw std::runtime_error("Selection has an incoming control link");
                    for(const char* field:{"patrol","targets"})for(const auto& ref:row.value(field,Json::array()))
                        if(ids.contains(ref))throw std::runtime_error("Selection has an incoming link; edit that relationship first");
                }
                auto& rows=candidate["entities"];rows.erase(std::remove_if(rows.begin(),rows.end(),[&](const Json& row){return ids.contains(row.at("id"));}),rows.end());
                if(candidate.contains("groups"))for(auto& group:candidate["groups"])
                    group.erase(std::remove_if(group.begin(),group.end(),[&](const Json& id){return ids.contains(id);}),group.end());
                PruneRegionHomes(partition,ids);
                selection_.clear();
                if(ids.contains(selected_))selected_=rows.empty()?"":rows[0].at("id").get<std::string>();
            } else throw std::runtime_error("Unknown authoring command");
        }
        CheckAuthorCapacity(candidate);
        document_=std::move(candidate);streamDraft_=std::move(partition);history_.Commit(AuthorSnapshot(),op);dirty_=true;structureDirty_=true;
        if(!created.empty()){selection_.clear();for(const auto& id:created)selection_.insert(id);selected_=created[0];}
        return {{"created",created},{"dirty",true},{"selection",selection_}};
    }
    Json StreamCommand(const std::string& op,const Json& args) {
        if(op=="get_streaming")return {{"draft",streamDraft_},{"saved_sha256",streamSavedSha_},{"dirty",dirty_},{"homes",DlRegionAuthoring::Homes(streamDraft_,document_)}};
        if(streamDraft_.is_null())throw std::runtime_error("This scene has no stream partition");
        if(engine_.IsPlaying()||Busy())throw std::runtime_error("Finish Play/build before editing regions");
        ObserveDocument();Json candidate=streamDraft_;
        auto& regions=candidate.at("regions");
        if(!regions.is_array())throw std::runtime_error("Partition regions must be an array; repair the manifest structure and reload");
        auto findRegion=[&](const std::string& id)->Json& {
            for(auto& row:regions)if(row.is_object()&&row.contains("id")&&row.at("id")==id)return row;
            throw std::runtime_error("Unknown region: "+id);
        };
        if(DlRegionAuthoring::Supports(op)) {
            candidate=DlRegionAuthoring::Apply(candidate,document_,op,args);
        }else if(op=="assign_region") {
            const std::string home=args.at("region");findRegion(home);
            const auto ids=SelectedIds(args);
            for(auto& row:regions){
                if(!row.is_object()||!row.contains("entities")||!row.at("entities").is_array())throw std::runtime_error("Region home entities must be an array; repair the manifest row and reload");
                auto& homes=row.at("entities");
                for(const auto& id:homes)if(!id.is_string())throw std::runtime_error("Region home IDs must be strings; repair the manifest row and reload");
                homes.erase(std::remove_if(homes.begin(),homes.end(),[&](const Json& id){return ids.contains(id.get<std::string>());}),homes.end());}
            for(const auto& id:ids)findRegion(home)["entities"].push_back(id);
        }else if(op=="set_region") {
            const std::string id=args.at("id");auto& row=findRegion(id);
            const auto& patch=args.at("patch");
            for(auto it=patch.begin();it!=patch.end();++it){
                if(it.key()=="bounds"||it.key()=="activation_bounds"){
                    if(!it.value().is_array()||it.value().size()!=2)throw std::runtime_error("Bounds need minimum and maximum XYZ");
                    for(int a=0;a<3;++a)if(!it.value()[0].is_array()||!it.value()[1].is_array()||
                        it.value()[0].size()!=3||it.value()[1].size()!=3||!it.value()[0][a].is_number()||!it.value()[1][a].is_number()||
                        !std::isfinite(it.value()[0][a].get<double>())||!std::isfinite(it.value()[1][a].get<double>())||
                        it.value()[0][a].get<double>()>=it.value()[1][a].get<double>())throw std::runtime_error("Minimum must be finite and below maximum");
                }else if(it.key()=="neighbors"||it.key()=="working_set"){
                    if(!it.value().is_array())throw std::runtime_error("Neighbors must be a region ID list");
                    std::set<std::string> seen;
                    for(const auto& value:it.value()){const auto peer=value.get<std::string>();findRegion(peer);
                        if((it.key()=="neighbors"&&peer==id)||!seen.insert(peer).second)throw std::runtime_error("Region IDs must be distinct; neighbors cannot include self");}
                    if(it.key()=="working_set"&&!seen.contains(id))throw std::runtime_error("Working set must include its own region");
                }else throw std::runtime_error("Only geometry/activation bounds, working set and neighbors can be changed here");
                row[it.key()]=it.value();
            }
        }else if(op=="set_streaming") {
            const auto& patch=args.at("patch");
            for(auto it=patch.begin();it!=patch.end();++it){
                if(it.key()=="entry_region")findRegion(it.value().get<std::string>());
                else if(it.key()=="visibility"){
                    if(it.value()!="door-distance"&&it.value()!="all-regions"&&it.value()!="authored-sets")throw std::runtime_error("Choose door-distance, all-regions or authored-sets visibility");
                }else throw std::runtime_error("Only entry region and visibility can be changed here");
                candidate[it.key()]=it.value();
            }
            if(candidate.value("visibility",std::string())=="authored-sets"){
                for(auto& row:regions)if(row.is_object()&&row.contains("id")){
                    if(!row.contains("activation_bounds")&&row.contains("bounds"))row["activation_bounds"]=row.at("bounds");
                    if(!row.contains("working_set"))row["working_set"]=Json::array({row.at("id")});
                }
                if(!candidate.contains("transitions"))candidate["transitions"]=Json::array();
            }
        }else if(op=="set_transition"||op=="add_transition"||op=="delete_transition") {
            if(!candidate.contains("transitions"))candidate["transitions"]=Json::array();
            auto& gates=candidate["transitions"];if(!gates.is_array())throw std::runtime_error("Transitions must be an array; repair the manifest and reload");
            const std::string id=args.at("id");auto gate=std::find_if(gates.begin(),gates.end(),[&](const Json& row){return row.is_object()&&row.value("id",std::string())==id;});
            if(op=="delete_transition"){if(gate==gates.end())throw std::runtime_error("Unknown transition");gates.erase(gate);}
            else {
                Json row=op=="add_transition"?Json{{"id",id}}:(gate==gates.end()?throw std::runtime_error("Unknown transition"):*gate);
                if(op=="add_transition"&&gate!=gates.end())throw std::runtime_error("Transition ID already exists");
                const auto& patch=args.at("patch");
                for(const auto& [key,value]:patch.items()){
                    if(key=="from"||key=="to")findRegion(value.get<std::string>());
                    else if(key=="door"){
                        if(value.is_null()){row.erase("door");continue;}
                        if(!value.is_string())throw std::runtime_error("A gate door needs a canonical door ID");
                        const auto* door=Entity(value.get<std::string>());
                        if(!door||door->at("kind")!="door")throw std::runtime_error("Choose an existing door object");
                    }
                    else if(key=="bounds"){
                        if(!value.is_array()||value.size()!=2)throw std::runtime_error("Gate bounds need two XYZ vectors");
                        for(const auto& point:value)CheckVector(point,"gate bounds",-1024,1024);
                        for(int axis=0;axis<3;++axis)if(value[0][axis].get<double>()>=value[1][axis].get<double>())throw std::runtime_error("Gate minimum must be below maximum");
                    }else throw std::runtime_error("Only gate endpoints, door and bounds can be changed");
                    row[key]=value;
                }
                if(op=="add_transition"){
                    if(!row.contains("from")||!row.contains("to")||!row.contains("bounds"))throw std::runtime_error("A new gate needs from/to/bounds");
                    gates.push_back(row);
                }else *gate=std::move(row);
            }
            if(candidate.value("visibility",std::string())=="authored-sets")for(auto& region:regions)
                if(region.is_object()&&region.contains("id")&&region.at("id").is_string()){
                    std::set<std::string> neighbors;
                    for(const auto& row:gates)if(row.is_object()&&row.contains("from")&&row.at("from")==region.at("id")&&row.contains("to")&&row.at("to").is_string())
                        neighbors.insert(row.at("to").get<std::string>());
                    region["neighbors"]=neighbors;
                }
        }else if(op=="fit_region_bounds") {
            const auto sourceStage=queue_/"bounds-scene.json",missionStage=queue_/"bounds-partition.json";
            AtomicWrite(sourceStage,document_.dump(2)+"\n");AtomicWrite(missionStage,candidate.dump(2)+"\n");
            auto result=ProjectCommand({L"fit_region_bounds",L"--level",source_.wstring(),L"--region",fs::path(args.at("id").get<std::string>()).wstring(),
                L"--staged",sourceStage.wstring(),L"--staged-mission",missionStage.wstring(),
                L"--expected-sha256",fs::path(sourceSavedSha_).wstring(),L"--expected-mission-sha256",fs::path(streamSavedSha_).wstring()});
            candidate=result.at("mission_document");
        }else throw std::runtime_error("Unknown region command");
        streamDraft_=std::move(candidate);history_.Commit(AuthorSnapshot(),op);UpdateAuthorDirty();
        structureDirty_=true;lastPreviewDraft_.clear();
        return {{"draft",streamDraft_},{"dirty",dirty_},{"validation","pending preview"}};
    }
    void DrawAuthoring() {
        if(BeginPanel("Blockout & Groups")) {
            ImGui::BeginDisabled(engine_.IsPlaying()||Busy());
            try {
                ImGui::InputFloat3("Block size (m)",blockSize_);
                for(const char* shape:{"box","wall","floor","platform","stairs","doorway"}) {
                    if(ImGui::Button(shape))AuthorCommand("add_blockout",{{"shape",shape},{"size",{blockSize_[0],blockSize_[1],blockSize_[2]}}});
                    ImGui::SameLine();
                }
                ImGui::NewLine();if(ImGui::Button("Add floor triangle"))AuthorCommand("add_floor",Json::object());
                ImGui::NewLine();ImGui::SeparatorText("Selection");
                ImGui::TextDisabled("%zu selected in Objects",selection_.size());
                ImGui::InputFloat3("Move selection",groupOffset_);ImGui::InputFloat("Snap spacing",&snapMetres_);
                if(ImGui::Button("Move"))AuthorCommand("transform_selection",{{"translation",{groupOffset_[0],groupOffset_[1],groupOffset_[2]}}});
                ImGui::SameLine();if(ImGui::Button("Snap"))AuthorCommand("transform_selection",{{"snap",snapMetres_}});
                ImGui::SameLine();if(ImGui::Button("Drop to surface"))AuthorCommand("drop_selection",Json::object());
                ImGui::InputFloat("Group yaw",&groupYaw_);if(ImGui::Button("Rotate group"))AuthorCommand("transform_selection",{{"yaw",groupYaw_}});
                ImGui::InputFloat("Group scale",&groupScale_);if(ImGui::Button("Scale group"))AuthorCommand("transform_selection",{{"scale",groupScale_}});
                if(ImGui::Button("Duplicate selection"))AuthorCommand("duplicate_entities",Json::object());
                ImGui::SameLine();if(ImGui::Button("Delete selection"))AuthorCommand("delete_entities",Json::object());
                ImGui::InputText("Group name",groupName_,sizeof(groupName_));
                if(ImGui::Button("Save group"))AuthorCommand("group_entities",{{"name",groupName_}});
                if(document_.contains("groups"))for(const auto& [name,ids]:document_["groups"].items())
                    if(ImGui::Button(name.c_str()))AuthorCommand("select_entities",{{"ids",ids}});
                ImGui::InputText("Prefab name",prefabName_,sizeof(prefabName_));
                if(ImGui::Button("Save selection as prefab"))AuthorCommand("save_prefab",{{"name",prefabName_}});
                if(document_.contains("encounter_prefabs"))for(const auto& [name,prefab]:document_["encounter_prefabs"].items()) {
                    if(ImGui::Button(("Place "+name).c_str())){AuthorCommand("place_prefab",{{"name",name}});break;}
                }
            }catch(const std::exception& e){status_=e.what();}
            ImGui::TextWrapped("%s",status_.c_str());ImGui::EndDisabled();
        }
        ImGui::End();
    }
    void DrawInteractions() {
        const auto* current=Entity(selected_);
        if(!current||current->at("kind")=="spawn"||current->at("kind")=="waypoint"||current->at("kind")=="light")return;
        if(ImGui::CollapsingHeader("Interaction & collision",ImGuiTreeNodeFlags_DefaultOpen)) {
            ImGui::BeginDisabled(engine_.IsPlaying()||Busy());
            try {
                const auto* selected=Entity(selected_);
                if(selected) {
                    const Json entity=*selected;const std::string kind=entity.at("kind");

                    if(kind=="control") {
                        Json refs=entity.value("targets",entity.contains("target")?Json::array({entity.at("target")}):Json::array());
                        const auto doors=document_["entities"];
                        for(const auto& row:doors)if(row.at("kind")=="door") {
                            bool linked=std::find(refs.begin(),refs.end(),row.at("id"))!=refs.end();
                            if(ImGui::Checkbox(row.at("id").get<std::string>().c_str(),&linked)) {
                                if(linked)refs.push_back(row.at("id"));else refs.erase(std::remove(refs.begin(),refs.end(),row.at("id")),refs.end());
                                AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{"targets",refs}}}});
                            }
                        }
                    }
                    if(kind=="loot"||kind=="objective") {
                        const char* field=kind=="loot"?"value":"required_loot";int value=entity.value(field,kind=="loot"?1:0);
                        if(ImGui::InputInt(kind=="loot"?"Loot value":"Required loot",&value))AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{field,std::max(0,value)}}}});
                        if(kind=="objective") {bool zone=entity.value("zone",false);if(ImGui::Checkbox("Complete on entering zone",&zone))AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{"zone",zone}}}});}
                        float radius=entity.value("interaction_radius",1.5f);if(ImGui::InputFloat("Interaction / zone radius",&radius)&&radius>=.05f&&radius<=128)AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{"interaction_radius",radius}}}});
                    }
                    if((kind=="static"||kind=="loot")&&entity.contains("model")&&!entity.contains("floor"))if(ImGui::Button(kind=="loot"?"Make decorative":"Make collectible"))AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{"kind",kind=="loot"?"static":"loot"}}}});
                    if(entity.contains("collider"))for(const char* field:{"center","half_size"}) {
                        auto proxy=entity.at("collider");float xyz[3]={proxy[field][0],proxy[field][1],proxy[field][2]};
                        if(ImGui::InputFloat3(field,xyz)){proxy[field]={xyz[0],xyz[1],xyz[2]};AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{"collider",proxy}}}});}
                    }
                    for(const char* field:{"model","material"})if(entity.contains(field)) {
                        const auto definitions=ResolvedDefinitions(std::string(field)=="model"?"assets":"materials");
                        if(ImGui::BeginCombo(field,entity.at(field).get<std::string>().c_str())) {
                            std::string choice;
                            for(const auto& row:definitions)if(ImGui::Selectable(row.at("id").get<std::string>().c_str()))choice=row.at("id");
                            ImGui::EndCombo();
                            if(!choice.empty())AuthorCommand("set_game_object",{{"id",selected_},{"patch",{{field,choice}}}});
                        }
                    }
                }
            }catch(const std::exception& e){status_=e.what();}
            ImGui::EndDisabled();
        }
    }
    void DrawFloorVertices() {
        const auto* entity=Entity(selected_);
        if(!entity||!entity->contains("floor"))return;
        if(!ImGui::CollapsingHeader("Floor triangle",ImGuiTreeNodeFlags_DefaultOpen))return;
        const auto source=entity->at("floor").at("vertices");const auto id=selected_;
        auto [iterator,inserted]=floorVertexInputs_.try_emplace(id);auto& input=iterator->second;
        if(inserted||input.source!=source){input=FloorVertexInput{};input.source=source;
            for(int vertex=0;vertex<3;++vertex)for(int axis=0;axis<3;++axis)input.points[vertex][axis]=source[vertex][axis];}
        ImGui::TextWrapped("Local XYZ vertices. Collision follows this triangle and its slope, including transformed placement. Enable Collision debug for its edges.");
        for(int vertex=0;vertex<3;++vertex){const auto label="Vertex "+std::to_string(vertex+1);input.pending|=ImGui::InputFloat3(label.c_str(),input.points[vertex]);}
        ImGui::BeginDisabled(!input.pending);
        if(ImGui::Button("Apply floor vertices")){
            Json vertices=Json::array();for(const auto& point:input.points)vertices.push_back({point[0],point[1],point[2]});
            Defer([this,id,vertices]{AuthorCommand("set_game_object",{{"id",id},{"patch",{{"floor",{{"vertices",vertices}}}}}});});
        }
        ImGui::EndDisabled();
        if(input.pending)ImGui::TextWrapped("Apply the vertices before saving or validating.");
    }
