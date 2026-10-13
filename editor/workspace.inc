    std::map<std::string,bool> panels_={{"Objects",true},{"Inspector",true},{"Levels",false},
        {"Blockout & Groups",false},{"Assets",false},{"Environment",false},{"Animation",false},
        {"Play Inspector",false},{"Build & Diagnostics",false},{"Audio Memory",false},{"Controls",false},
        {"Renderer resources",false},{"Streaming",false}};
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
    void DrawStreaming() {
        if(BeginPanel("Streaming")) {
            if(const auto* info=StreamingInfo()) {
                ImGui::TextWrapped("Experimental stream mission");
                ImGui::TextWrapped("%s",info->value("preview_mode",std::string()).c_str());
                ImGui::TextWrapped("%s",info->value("native_play_reason",std::string()).c_str());
                ImGui::Separator();
                ImGui::TextWrapped("Partition: %s",info->value("mission_manifest",std::string()).c_str());
                ImGui::TextWrapped("Visibility: %s",info->value("visibility",std::string("door-distance")).c_str());
                if(info->value("visibility",std::string())=="all-regions")
                    ImGui::TextWrapped("Open exterior: all region chunks stay resident until visibility is proven.");
                const auto& validation=info->at("status");
                if(validation.value("valid",false)) {
                    ImGui::Text("Directory %u bytes | ROM %u bytes",validation.value("directory_bytes",0u),validation.value("package_bytes",0u));
                    ImGui::Text("Neighbor chunk overlap: %u bytes",validation.value("peak_neighbor_overlap_chunk_bytes",0u));
                    ImGui::TextWrapped("Renderer, stream banks/slots, gameplay, audio and display costs are additional.");
                } else ImGui::TextWrapped("Region cook needs repair: %s",validation.value("error",std::string()).c_str());
                if(info->at("regions").is_array())for(const auto& region:info->at("regions")) {
                    if(!region.is_object())continue;
                    const auto id=region.value("id",std::string("Invalid region"));
                    if(ImGui::TreeNode(id.c_str())) {
                        ImGui::TextWrapped("Bounds and authored home IDs: %s",region.dump(2).c_str());
                        ImGui::TreePop();
                    }
                }
                if(!selected_.empty()) {
                    std::string home;
                    if(info->at("regions").is_array())for(const auto& region:info->at("regions"))
                        if(region.is_object()&&region.contains("entities")&&region.at("entities").is_array())
                            for(const auto& entity:region.at("entities"))if(entity==selected_)home=region.value("id",std::string());
                    ImGui::TextWrapped("Selected %s | home: %s",selected_.c_str(),home.empty()?"UNASSIGNED - assign before ROM cook":home.c_str());
                }
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
                for(const char* name:{"Play Inspector","Build & Diagnostics","Audio Memory","Renderer resources","Streaming"})
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
            if(ImGui::MenuItem("Controls"))ShowPanel("Controls");
            ImGui::EndMenu();
        }
        ImGui::Separator();
        if(!playing) {
            ImGui::BeginDisabled(locked||StreamingInfo()!=nullptr);
            if(ImGui::SmallButton("Play")){nativeStart_=testStart_;nativeRequested_=true;}
            ImGui::EndDisabled();
            if(StreamingInfo()) {
                if(ImGui::SmallButton("Stream regions"))ShowPanel("Streaming");
                if(ImGui::IsItemHovered())ImGui::SetTooltip("Native Play is unavailable for stream missions. Run level in Ares uses the ROM streaming pipeline.");
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
        if(panels_.at("Streaming"))DrawStreaming();
        if(!engine_.IsPlaying()) {
            if(selected_.empty())selection_.clear();
            else if(!selection_.contains(selected_)){selection_.clear();selection_.insert(selected_);}
            std::optional<EntityId> selected;
            for(const auto& [id,name]:previewIds_)if(name==selected_){selected=id;break;}
            engine_.SetEditorSelection(Busy()?std::nullopt:selected);
        }
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
