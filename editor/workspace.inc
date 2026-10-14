    std::map<std::string,bool> panels_={{"Objects",true},{"Inspector",true},{"Levels",false},
        {"Blockout & Groups",false},{"Assets",false},{"Environment",false},{"Animation",false},
        {"Play Inspector",false},{"Build & Diagnostics",false},{"Audio Memory",false},{"Controls",false},
        {"Renderer resources",false},{"Streaming",false},{"Project Status",false}};
    std::vector<std::function<void()>> uiActions_;
    std::string savedPanels_,focusPanel_;
    char objectFilter_[96]{};
    void LoadWorkspace() {
        const auto path=queue_/"workspace-v2.json";
        if(fs::exists(path))try {
            auto data=ReadJson(path);
            for(auto& [name,visible]:panels_)if(data.contains(name)&&data[name].is_boolean())visible=data[name];
        }catch(const std::exception&){}
        savedPanels_=Json(panels_).dump();
    }
    void ShowPanel(const std::string& name,bool visible=true) {
        if(!panels_.contains(name))throw std::runtime_error("Unknown panel: "+name);
        panels_.at(name)=visible;if(visible)focusPanel_=name;
        engine_.SetEditorResourcePanelsVisible(panels_.at("Renderer resources"));
    }
    bool BeginPanel(const char* name) {
        ImGui::SetNextWindowSize(ImVec2(360,540),ImGuiCond_FirstUseEver);
        return ImGui::Begin(name,&panels_.at(name));
    }
    void ResetWorkspace() {
        for(auto& [name,visible]:panels_)visible=name=="Objects"||name=="Inspector";
        engine_.SetEditorResourcePanelsVisible(false);engine_.ResetEditorLayout();
    }
    void Defer(std::function<void()> action){uiActions_.push_back(std::move(action));}
    const Json* StreamingInfo() const {
        if(report_.is_object()&&report_.contains("streaming")&&report_.at("streaming").is_object())return &report_.at("streaming");
        const auto* entry=LevelEntry(source_.filename().string());
        if(entry&&entry->contains("streaming")&&entry->at("streaming").is_object())return &entry->at("streaming");
        return nullptr;
    }
    static std::string StreamText(const Json& row,const char* key,const std::string& fallback={}) {
        return row.is_object()&&row.contains(key)&&row.at(key).is_string()?row.at(key).get<std::string>():fallback;
    }
    static bool StreamRegion(const Json& row) {return !StreamText(row,"id").empty();}
    static bool ReadStreamBounds(const Json& region,float minimum[3],float maximum[3]) {
        if(!region.contains("bounds"))return false;
        const auto& bounds=region.at("bounds");
        if(!bounds.is_array()||bounds.size()!=2||!bounds[0].is_array()||!bounds[1].is_array()||bounds[0].size()!=3||bounds[1].size()!=3)return false;
        for(int axis=0;axis<3;++axis)for(int end=0;end<2;++end)
            if(!bounds[end][axis].is_number()||!std::isfinite(bounds[end][axis].get<float>()))return false;
        for(int axis=0;axis<3;++axis){minimum[axis]=bounds[0][axis];maximum[axis]=bounds[1][axis];}
        return true;
    }
    struct StreamBoundsInput {
        Json source;
        float minimum[3]={-1,-1,-1},maximum[3]={1,1,1};
        bool pending=false;
    };
    std::map<std::string,StreamBoundsInput> streamBoundsInputs_;
    char newTransitionId_[65]="new-gate";
    std::string newTransitionFrom_,newTransitionTo_;
    float newGateMinimum_[3]={-.03f,-1,-.5f},newGateMaximum_[3]={.03f,3,.5f};
    void DrawPolicyBounds(const std::string& id,const std::string& key,const Json& bounds,const char* label,const std::string& op) {
        ImGui::PushID((op+":"+id+":"+key).c_str());
        auto [iterator,inserted]=streamBoundsInputs_.try_emplace(op+":"+id+":"+key);auto& input=iterator->second;
        Json row={{"bounds",bounds}};float minimum[3],maximum[3];const bool valid=ReadStreamBounds(row,minimum,maximum);
        if(inserted||input.source!=bounds){input=StreamBoundsInput{};input.source=bounds;ReadStreamBounds(row,input.minimum,input.maximum);}
        ImGui::TextUnformatted(label);if(!valid)ImGui::TextWrapped("Repair these bounds with two finite XYZ vectors.");
        input.pending|=ImGui::InputFloat3("Minimum",input.minimum,"%.3f");
        input.pending|=ImGui::InputFloat3("Maximum",input.maximum,"%.3f");
        ImGui::BeginDisabled(!input.pending&&valid);
        if(ImGui::Button("Apply bounds")){
            Json value={{input.minimum[0],input.minimum[1],input.minimum[2]},{input.maximum[0],input.maximum[1],input.maximum[2]}};
            Defer([this,id,key,value,op]{StreamCommand(op,{{"id",id},{"patch",{{key,value}}}});});
        }
        ImGui::EndDisabled();if(input.pending)ImGui::TextWrapped("Bounds have unapplied edits.");ImGui::PopID();
    }
    void DrawStreaming() {
        if(BeginPanel("Streaming")) {
            if(const auto* info=StreamingInfo()) {
                DrawStreamPreviewControls();
                ImGui::Separator();
                ImGui::TextWrapped("Partition: %s",info->value("mission_manifest",std::string()).c_str());
                const Json emptyRegions=Json::array();
                const bool regionList=streamDraft_.is_object()&&streamDraft_.contains("regions")&&streamDraft_.at("regions").is_array();
                const auto& regions=regionList?streamDraft_.at("regions"):emptyRegions;
                if(!regionList)ImGui::TextWrapped("Partition regions must be an array. Repair the manifest structure and reload; the scene remains editable.");
                const auto visibility=StreamText(streamDraft_,"visibility","door-distance");
                ImGui::BeginDisabled(engine_.IsPlaying()||Busy()||!pendingAction_.empty());
                DrawRegionLifecycle(regions);
                if(ImGui::BeginCombo("Visibility",visibility.c_str())){
                    for(const char* value:{"door-distance","all-regions","authored-sets"})if(ImGui::Selectable(value,visibility==value))
                        Defer([this,value]{StreamCommand("set_streaming",{{"patch",{{"visibility",value}}}});});
                    ImGui::EndCombo();
                }
                const auto entry=StreamText(streamDraft_,"entry_region");
                if(ImGui::BeginCombo("Entry region",entry.c_str())){
                    for(const auto& row:regions)if(StreamRegion(row)){const std::string id=StreamText(row,"id");
                        if(ImGui::Selectable(id.c_str(),entry==id))Defer([this,id]{StreamCommand("set_streaming",{{"patch",{{"entry_region",id}}}});});}
                    ImGui::EndCombo();
                }
                if(visibility=="all-regions")
                    ImGui::TextWrapped("Open exterior: all region chunks stay resident until visibility is proven.");
                if(visibility=="authored-sets")ImGui::TextWrapped("Authored conservative working sets: one nearby transition target is prefetched. Geometry/collision remain bounded per reachable set.");
                if(dirty_)ImGui::TextWrapped("Scene/partition draft has unsaved changes. Undo/Redo and Save + Cook apply to both files.");
                if(ImGui::Button("Validate draft"))Defer([this]{RefreshDraft();});
                const auto& validation=info->at("status");
                if(validation.value("valid",false)) {
                    ImGui::Text("Directory %u bytes | ROM %u bytes",validation.value("directory_bytes",0u),validation.value("package_bytes",0u));
                    ImGui::Text("Neighbor chunk overlap: %u bytes",validation.value("peak_neighbor_overlap_chunk_bytes",0u));
                    if(visibility=="authored-sets")ImGui::Text("Worst resident regions: %u / 5",validation.value("max_resident_regions",0u));
                    ImGui::TextWrapped("Renderer, stream banks/slots, gameplay, audio and display costs are additional.");
                } else ImGui::TextWrapped("Region cook needs repair: %s",validation.value("error",std::string()).c_str());
                for(const auto& region:regions) {
                    if(!StreamRegion(region)){ImGui::TextWrapped("Malformed region: each row needs an object with a string ID. Repair the manifest and reload.");continue;}
                    const auto id=StreamText(region,"id");
                    const Json savedBounds=region.contains("bounds")?region.at("bounds"):Json();
                    auto [boundsInput,inserted]=streamBoundsInputs_.try_emplace(id);
                    auto& input=boundsInput->second;
                    if(inserted||input.source!=savedBounds){input=StreamBoundsInput{};input.source=savedBounds;ReadStreamBounds(region,input.minimum,input.maximum);}
                    float checkedMinimum[3],checkedMaximum[3];
                    const bool boundsValid=ReadStreamBounds(region,checkedMinimum,checkedMaximum);
                    if(ImGui::TreeNodeEx(id.c_str(),ImGuiTreeNodeFlags_DefaultOpen)) {
                        DrawStreamHandles("region",id,"bounds");
                        if(!boundsValid)ImGui::TextWrapped("Bounds need two finite XYZ vectors. Apply replacement bounds below or fit bounds to owned objects.");
                        input.pending|=ImGui::InputFloat3("Minimum (m)",input.minimum,"%.3f");
                        input.pending|=ImGui::InputFloat3("Maximum (m)",input.maximum,"%.3f");
                        if(input.pending)ImGui::TextWrapped("Bounds have unapplied edits. Apply bounds before saving or validating the partition.");
                        ImGui::BeginDisabled(!input.pending&&boundsValid);
                        if(ImGui::Button("Apply bounds")){const Json bounds={{input.minimum[0],input.minimum[1],input.minimum[2]},{input.maximum[0],input.maximum[1],input.maximum[2]}};
                            Defer([this,id,bounds]{StreamCommand("set_region",{{"id",id},{"patch",{{"bounds",bounds}}}});});}
                        ImGui::EndDisabled();
                        if(ImGui::Button("Fit bounds to owned objects"))Defer([this,id]{StreamCommand("fit_region_bounds",{{"id",id}});});
                        if(visibility=="authored-sets"){
                            DrawStreamHandles("region",id,"activation_bounds");
                            DrawPolicyBounds(id,"activation_bounds",region.contains("activation_bounds")?region.at("activation_bounds"):Json(),"Player activation bounds","set_region");
                            ImGui::TextWrapped("Conservative visible working set (include every region visible from this activation area):");
                            Json working=region.contains("working_set")&&region.at("working_set").is_array()?region.at("working_set"):Json::array({id});
                            for(const auto& peer:regions)if(StreamRegion(peer)){
                                const auto other=StreamText(peer,"id");bool included=std::find(working.begin(),working.end(),Json(other))!=working.end();
                                ImGui::BeginDisabled(other==id);
                                if(ImGui::Checkbox(("Visible "+other).c_str(),&included)){
                                    if(included)working.push_back(other);else working.erase(std::remove(working.begin(),working.end(),Json(other)),working.end());
                                    Defer([this,id,working]{StreamCommand("set_region",{{"id",id},{"patch",{{"working_set",working}}}});});
                                }
                                ImGui::EndDisabled();
                            }
                        }
                        if(region.contains("entities")&&region.at("entities").is_array()&&std::all_of(region.at("entities").begin(),region.at("entities").end(),[](const Json& id){return id.is_string();}))ImGui::Text("Home objects: %u",(unsigned)region.at("entities").size());
                        else ImGui::TextWrapped("Home entities must be an array of object IDs. Repair this manifest row and reload.");
                        ImGui::TextUnformatted("Prefetch neighbors:");
                        const bool neighborsValid=region.contains("neighbors")&&region.at("neighbors").is_array()&&std::all_of(region.at("neighbors").begin(),region.at("neighbors").end(),[](const Json& id){return id.is_string();});
                        if(!neighborsValid)ImGui::TextWrapped("Neighbors must be an array of region IDs; choosing a link replaces the malformed list.");
                        for(const auto& peer:regions)if(StreamRegion(peer)){
                            const std::string other=StreamText(peer,"id");if(other==id)continue;
                            Json neighbors=neighborsValid?region.at("neighbors"):Json::array();
                            bool selected=std::find(neighbors.begin(),neighbors.end(),Json(other))!=neighbors.end();
                            if(ImGui::Checkbox(other.c_str(),&selected)){
                                if(selected)neighbors.push_back(other);else neighbors.erase(std::remove(neighbors.begin(),neighbors.end(),Json(other)),neighbors.end());
                                Defer([this,id,neighbors]{StreamCommand("set_region",{{"id",id},{"patch",{{"neighbors",neighbors}}}});});
                            }
                        }
                        ImGui::TreePop();
                    }
                }
                if(visibility=="authored-sets"){
                    ImGui::SeparatorText("Directed transition gates");
                    ImGui::TextWrapped("Gate blockers must span the player's body and jump range. Bounds are movement gates; working sets must independently preserve visible architecture.");
                    const Json emptyGates=Json::array();
                    const auto& gates=streamDraft_.contains("transitions")&&streamDraft_.at("transitions").is_array()?streamDraft_.at("transitions"):emptyGates;
                    for(const auto& gate:gates){
                        const auto id=StreamText(gate,"id");if(id.empty()){ImGui::TextWrapped("Malformed transition; repair its ID and endpoints in the manifest.");continue;}
                        if(ImGui::TreeNodeEx(("Gate "+id).c_str(),ImGuiTreeNodeFlags_DefaultOpen)){
                            DrawStreamHandles("gate",id,"bounds");
                            for(const char* key:{"from","to"}){
                                const auto selected=StreamText(gate,key);
                                if(ImGui::BeginCombo(key,selected.c_str())){
                                    for(const auto& region:regions)if(StreamRegion(region)){const auto peer=StreamText(region,"id");
                                        if(ImGui::Selectable(peer.c_str(),peer==selected))Defer([this,id,key,peer]{StreamCommand("set_transition",{{"id",id},{"patch",{{key,peer}}}});});}
                                    ImGui::EndCombo();
                                }
                            }
                            const auto door=StreamText(gate,"door");
                            if(ImGui::BeginCombo("Door",door.empty()?"Open gate":door.c_str())){
                                if(ImGui::Selectable("Open gate",door.empty()))Defer([this,id]{StreamCommand("set_transition",{{"id",id},{"patch",{{"door",nullptr}}}});});
                                for(const auto& object:document_.at("entities"))if(object.at("kind")=="door"){
                                    const std::string peer=object.at("id");
                                    if(ImGui::Selectable(peer.c_str(),peer==door))Defer([this,id,peer]{StreamCommand("set_transition",{{"id",id},{"patch",{{"door",peer}}}});});
                                }
                                ImGui::EndCombo();
                            }
                            DrawPolicyBounds(id,"bounds",gate.contains("bounds")?gate.at("bounds"):Json(),"Gate blocker bounds","set_transition");
                            if(ImGui::Button("Delete gate"))Defer([this,id]{StreamCommand("delete_transition",{{"id",id}});});
                            ImGui::TreePop();
                        }
                    }
                    ImGui::InputText("New gate ID",newTransitionId_,sizeof newTransitionId_);
                    ImGui::Checkbox("Create both travel directions",&pairedGate_);
                    if(pairedGate_)ImGui::InputText("Reverse gate ID",reverseGateId_,sizeof reverseGateId_);
                    for(const char* key:{"from","to"}){
                        auto& selected=std::string(key)=="from"?newTransitionFrom_:newTransitionTo_;
                        if(ImGui::BeginCombo((std::string("New gate ")+key).c_str(),selected.c_str())){
                            for(const auto& region:regions)if(StreamRegion(region)){const auto peer=StreamText(region,"id");if(ImGui::Selectable(peer.c_str(),peer==selected))selected=peer;}
                            ImGui::EndCombo();
                        }
                    }
                    ImGui::InputFloat3("New gate minimum",newGateMinimum_);ImGui::InputFloat3("New gate maximum",newGateMaximum_);
                    if(ImGui::Button("Add directed gate")){
                        const std::string id=newTransitionId_,from=newTransitionFrom_,to=newTransitionTo_;
                        const std::string reverse=reverseGateId_;const bool pair=pairedGate_;
                        const Json bounds={{newGateMinimum_[0],newGateMinimum_[1],newGateMinimum_[2]},{newGateMaximum_[0],newGateMaximum_[1],newGateMaximum_[2]}};
                        Defer([this,id,from,to,reverse,pair,bounds]{
                            if(pair)StreamCommand("add_transition_pair",{{"id",id},{"reverse_id",reverse},{"from",from},{"to",to},{"bounds",bounds}});
                            else StreamCommand("add_transition",{{"id",id},{"patch",{{"from",from},{"to",to},{"bounds",bounds}}}});
                        });
                    }
                }
                if(!selected_.empty()) {
                    const auto home=ObjectRegionHome(selected_);
                    ImGui::TextWrapped("Selected %s | home: %s",selected_.c_str(),home.empty()?"UNASSIGNED - assign before ROM cook":home.c_str());
                    if(ImGui::BeginCombo("Selection home",home.empty()?"Unassigned":home.c_str())){
                        for(const auto& region:regions)if(StreamRegion(region)){const std::string id=StreamText(region,"id");
                            if(ImGui::Selectable(id.c_str(),home==id))Defer([this,id]{StreamCommand("assign_region",{{"region",id}});});}
                        ImGui::EndCombo();
                    }
                }
                ImGui::TextWrapped("Drag the selected minimum, maximum or center handle with the viewport translation gizmo. Each completed drag is one paired Undo step.");
                ImGui::EndDisabled();
            } else ImGui::TextWrapped("This scene uses the resident level format. Streaming partition controls are experimental.");
        }
        ImGui::End();
    }
    void DrawWorkspaceMenu() {
        const bool playing=engine_.IsPlaying(),locked=playing||Busy()||!pendingAction_.empty();
        if(ImGui::BeginMenu("File")) {
            if(ImGui::MenuItem("Levels...",nullptr,false,!locked))ShowPanel("Levels");
            if(ImGui::BeginMenu("Open level",!locked)) {
                for(const auto& row:catalog_.at("levels")) {
                    const std::string file=row.at("file"),label=LevelLabel(row.at("title"));
                    if(ImGui::MenuItem((label+"##"+file).c_str(),nullptr,file==source_.filename().string()))Defer([this,file]{QueueAction({{"op","open"},{"file",file}});});
                }
                ImGui::EndMenu();
            }
            if(ImGui::MenuItem("Save + Cook","Ctrl+S",false,!locked))Defer([this]{Save(false);});
            if(ImGui::MenuItem("Reload saved level","Ctrl+R",false,!locked))Defer([this]{QueueAction({{"op","open"},{"file",source_.filename().string()}});});
            ImGui::Separator();
            if(ImGui::MenuItem("Exit"))Defer([this]{QueueAction({{"op","close"}});});
            ImGui::EndMenu();
        }
        if(ImGui::BeginMenu("Edit",!locked)) {
            if(ImGui::MenuItem("Undo","Ctrl+Z",false,history_.CanUndo()))Defer([this]{AuthorCommand("undo",Json::object());});
            if(ImGui::MenuItem("Redo","Ctrl+Y",false,history_.CanRedo()))Defer([this]{AuthorCommand("redo",Json::object());});
            if(ImGui::MenuItem("Duplicate selection","Shift+D",false,!selected_.empty()))Defer([this]{AuthorCommand("duplicate_entities",Json::object());});
            if(ImGui::MenuItem("Delete selection","X",false,!selected_.empty()))Defer([this]{AuthorCommand("delete_entities",Json::object());});
            ImGui::EndMenu();
        }
        if(ImGui::BeginMenu("Create",!locked)) {
            if(ImGui::MenuItem("Blockout & groups..."))ShowPanel("Blockout & Groups");
            if(ImGui::MenuItem("Assets..."))ShowPanel("Assets");
            ImGui::Separator();
            if(ImGui::MenuItem("Enemy"))Defer([this]{AddEnemy(Json::object());});
            if(ImGui::MenuItem("Waypoint"))Defer([this]{AddWaypoint(Json::object());});
            for(const char* kind:{"door","control","loot","objective","light"})
                if(ImGui::MenuItem(kind))Defer([this,kind]{AuthorCommand("add_game_object",{{"kind",kind}});});
            ImGui::EndMenu();
        }
        if(ImGui::BeginMenu("Window")) {
            for(const char* name:{"Objects","Inspector","Levels","Blockout & Groups","Assets","Environment","Animation"})
                if(ImGui::MenuItem(name,nullptr,panels_.at(name),!playing))ShowPanel(name,!panels_.at(name));
            if(ImGui::BeginMenu("Diagnostics")) {
                for(const char* name:{"Play Inspector","Build & Diagnostics","Project Status","Audio Memory","Renderer resources","Streaming"})
                    if(ImGui::MenuItem(name,nullptr,panels_.at(name),!playing||std::string(name)!="Renderer resources"))ShowPanel(name,!panels_.at(name));
                ImGui::EndMenu();
            }
            ImGui::Separator();
            if(ImGui::MenuItem("Restore default workspace"))ResetWorkspace();
            ImGui::EndMenu();
        }
        if(ImGui::BeginMenu("View")) {
            if(ImGui::BeginMenu("Transform mode",!playing)) {
                for(int i=0;i<static_cast<int>(TransformMode::COUNT);++i) {
                    auto mode=static_cast<TransformMode>(i);
                    if(ImGui::MenuItem(TransformModeNames[i].data(),nullptr,engine_.GetEditorTransformMode()==mode))engine_.SetEditorTransformMode(mode);
                }
                ImGui::EndMenu();
            }
            ImGui::MenuItem("Collision proxies",nullptr,&debugCollision_);
            ImGui::MenuItem("Guard sight",nullptr,&debugSight_);
            ImGui::EndMenu();
        }
        if(ImGui::BeginMenu("Build",!locked)) {
            if(ImGui::MenuItem("Build ROM"))Defer([this]{Save(true);});
            if(ImGui::MenuItem("Run level in Ares"))Defer([this]{Save(true,{},"level");});
            if(ImGui::MenuItem("Run game menu in Ares"))Defer([this]{Save(true,{},"menu");});
            if(ImGui::MenuItem("Build diagnostics..."))ShowPanel("Build & Diagnostics");
            ImGui::EndMenu();
        }
        if(ImGui::BeginMenu("Help")) {
            if(ImGui::MenuItem("Project status"))ShowPanel("Project Status");
            if(ImGui::MenuItem("Controls"))ShowPanel("Controls");
            ImGui::EndMenu();
        }
        ImGui::Separator();
        if(!playing) {
            ImGui::BeginDisabled(locked);
            if(ImGui::SmallButton("Play")){nativeStart_=testStart_;nativeRequested_=true;}
            ImGui::EndDisabled();
            if(StreamingInfo()) {
                if(ImGui::SmallButton("Stream regions"))ShowPanel("Streaming");
                if(ImGui::IsItemHovered())ImGui::SetTooltip("Native Play and Ares use the same mission package, stream coordinator and collision banks.");
            }
        } else {
            if(ImGui::SmallButton(engine_.IsPlayPaused()?"Resume":"Pause"))Defer([this]{engine_.SetPlayPaused(!engine_.IsPlayPaused());});
            ImGui::BeginDisabled(!engine_.IsPlayPaused());
            if(ImGui::SmallButton("Step"))Defer([this]{engine_.StepPlayMode();});
            ImGui::EndDisabled();
            if(ImGui::SmallButton("Restart"))Defer([this]{NativeCommand("restart_play",Json::object());});
            if(ImGui::SmallButton("Stop"))Defer([this]{engine_.ExitPlayMode();});
            if(ImGui::SmallButton("Inspect"))ShowPanel("Play Inspector");
        }
        const std::string title=LevelLabel(document_.at("title"));
        ImGui::TextDisabled("%s%s%s",title.c_str(),dirty_?" *":"",Busy()?" | Working...":"");
        if(!status_.empty()) {
            ImGui::TextDisabled("(?)");if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",status_.c_str());
        }
    }
    void FinishWorkspace() {
        UpdateStreamObjectSelection();
        if(panels_.at("Streaming"))DrawStreaming();
        if(panels_.at("Project Status"))DrawProjectStatus();else projectStatusVisible_=false;
        if(!engine_.IsPlaying()) {
            if(selected_.empty())selection_.clear();
            else if(!selection_.contains(selected_)){selection_.clear();selection_.insert(selected_);}
            std::optional<EntityId> selected;
            for(const auto& [id,name]:previewIds_)if(name==selected_){selected=id;break;}
            engine_.SetEditorSelection(Busy()?std::nullopt:streamBoundsHandle_?streamBoundsHandle_:selected);
        }
        DrawStreamViewportStatus();
        if(!focusPanel_.empty()) {
            if(auto* window=ImGui::FindWindowByName(focusPanel_.c_str());window&&window->Active) {
                ImGui::SetWindowFocus(window->Name);
                if(window->DockNode){window->DockNode->SelectedTabId=window->TabId;
                    if(auto* tabs=window->DockNode->TabBar)tabs->SelectedTabId=tabs->NextSelectedTabId=window->TabId;}
                focusPanel_.clear();
            }
        }
        const auto state=Json(panels_).dump();
        if(state!=savedPanels_)try{AtomicWrite(queue_/"workspace-v2.json",state);savedPanels_=state;}catch(const std::exception& e){status_=e.what();}
    }
