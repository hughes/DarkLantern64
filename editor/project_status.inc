    std::future<Json> projectStatusJob_;
    Json projectStatus_=Json::object(),projectStatusCaptured_=Json::object(),projectStatusPending_=Json::object();
    std::string projectStatusRequest_,projectStatusError_;
    bool projectStatusVisible_=false,projectStatusDraftStale_=false;
    std::chrono::steady_clock::time_point projectStatusDraftChecked_{};

    Json ProjectStatusDraft() const {
        Json draft={{"scene_file",fs::relative(source_,root_).generic_string()},{"scene",document_}};
        if(!streamDraft_.is_null()) {
            draft["partition_file"]=fs::relative(streamPath_,root_).generic_string();
            draft["partition"]=streamDraft_;
        }
        return draft;
    }
    bool ProjectStatusDraftChanged() const {
        if(projectStatusCaptured_.empty())return false;
        if(projectStatusCaptured_.value("scene_file",std::string())!=fs::relative(source_,root_).generic_string()||
           projectStatusCaptured_.at("scene")!=document_)return true;
        if(streamDraft_.is_null())return projectStatusCaptured_.contains("partition");
        return !projectStatusCaptured_.contains("partition")||projectStatusCaptured_.at("partition")!=streamDraft_||
            projectStatusCaptured_.value("partition_file",std::string())!=fs::relative(streamPath_,root_).generic_string();
    }
    Json ProjectStatusState() const {
        Json result=projectStatus_;
        result["editor_observation"]={{"collecting",projectStatusJob_.valid()},
            {"draft_changed_since_collection",projectStatusDraftStale_},{"error",projectStatusError_},
            {"captured_scene_file",projectStatusCaptured_.value("scene_file",std::string())}};
        return result;
    }
    void RefreshProjectStatus(const std::string& request={},bool doctor=true) {
        if(projectStatusJob_.valid())throw std::runtime_error("Project status is already being checked.");
        const auto snapshot=ProjectStatusDraft();
        const auto directory=queue_/"status"/std::to_string(++projectStatusSequence_);
        fs::create_directories(directory);AtomicWrite(directory/"draft.json",snapshot.dump(2)+"\n");
        projectStatusPending_=snapshot;projectStatusRequest_=request;projectStatusError_.clear();
        WriteEditorStatusState();
        const auto root=root_;
        projectStatusJob_=std::async(std::launch::async,[root,directory,doctor] {
            std::vector<std::wstring> command{L"python",(root/"tools/project_status.py").wstring(),
                L"--project",root.wstring(),L"--draft-file",(directory/"draft.json").wstring()};
            if(!doctor)command.push_back(L"--no-doctor");
            const auto output=directory/"result.json";
            if(Run(root,command,output,120000))throw std::runtime_error(Tail(output));
            return ReadJson(output);
        });
    }
    uint64_t projectStatusSequence_=0;
    void PollProjectStatus() {
        const auto now=std::chrono::steady_clock::now();
        if(now-projectStatusDraftChecked_>=std::chrono::milliseconds(500)) {
            projectStatusDraftChecked_=now;
            projectStatusDraftStale_=ProjectStatusDraftChanged();
        }
        if(!projectStatusJob_.valid()||projectStatusJob_.wait_for(std::chrono::seconds(0))!=std::future_status::ready)return;
        const auto request=projectStatusRequest_;projectStatusRequest_.clear();
        try {
            auto result=projectStatusJob_.get();
            if(!result.is_object()||result.value("schema_version",0)!=1)
                throw std::runtime_error("Project status returned an unsupported response.");
            projectStatus_=std::move(result);
            projectStatusCaptured_=std::move(projectStatusPending_);
            projectStatusDraftStale_=ProjectStatusDraftChanged();
            Respond(request,true,ProjectStatusState());
        }catch(const std::exception& error) {
            projectStatusError_=error.what();
            Respond(request,false,{{"error",projectStatusError_}});
        }
    }
    static Json StatusString(const Json& object,const char* key) {
        return object.is_object()&&object.contains(key)&&object.at(key).is_string()?object.at(key):Json(nullptr);
    }
    Json ProjectSessionIdentity() const {
        FILETIME creation{},exit{},kernel{},user{};
        const bool known=GetProcessTimes(GetCurrentProcess(),&creation,&exit,&kernel,&user)!=0;
        const uint64_t ticks=(uint64_t(creation.dwHighDateTime)<<32)|creation.dwLowDateTime;
        Json identity={{"process_creation_filetime",known?Json(ticks):Json(nullptr)},
            {"process_identity_status",known?"known":"unknown"},{"source",source_.string()},
            {"source_saved_sha256",sourceSavedSha_.empty()?Json(nullptr):Json(sourceSavedSha_)},
            {"partition",streamPath_.empty()?Json(nullptr):Json(streamPath_.string())},
            {"partition_saved_sha256",streamSavedSha_.empty()?Json(nullptr):Json(streamSavedSha_)},
            {"cooked_report_source_sha256",StatusString(report_,"source_sha256")},
            {"package_sha256",StatusString(streamCookReport_,"package_sha256")},{"build_id",nullptr}};
        Json native={{"active",native_&&engine_.IsPlaying()},{"mode",engine_.IsPlaying()?(engine_.IsPlayPaused()?"paused":"playing"):"edit"},
            {"play_fingerprint",native_?StatusString(native_->package,"fingerprint"):Json(nullptr)},
            {"package_sha256",nullptr},{"renderer_source_sha256",nullptr},{"build_id",nullptr}};
        if(native_&&native_->package.contains("stream")) {
            native["package_sha256"]=StatusString(native_->package.at("stream"),"package_sha256");
            native["renderer_source_sha256"]=StatusString(native_->package.at("stream"),"renderer_source_sha256");
        }
        Json preview={{"active",StreamPreviewActive()},{"mode",streamPreview_?"cooked-runtime-preview":"full-authoring"},
            {"play_fingerprint",streamPreview_?StatusString(streamPreview_->package,"fingerprint"):Json(nullptr)},
            {"package_sha256",streamPreview_?StatusString(streamPreview_->package.at("stream"),"package_sha256"):Json(nullptr)},
            {"source_saved_sha256",streamPreview_?Json(streamPreviewSourceSha_):Json(nullptr)},
            {"partition_saved_sha256",streamPreview_?Json(streamPreviewPartitionSha_):Json(nullptr)},{"build_id",nullptr}};
        identity["native_play"]=std::move(native);identity["stream_preview"]=std::move(preview);
        return identity;
    }
    void WriteEditorStatusState() const {
        Json state={{"pid",GetCurrentProcessId()},{"frame",frame_},{"dirty",dirty_},
            {"busy",Busy()},{"selected",selected_},{"status",status_},{"layout",Layout()}};
        state.update(ProjectSessionIdentity());AtomicWrite(queue_/"state.json",state.dump(2));
    }
    static std::string StatusText(const Json& object,const char* key,const std::string& fallback="unknown") {
        if(!object.is_object()||!object.contains(key)||object.at(key).is_null())return fallback;
        return object.at(key).is_string()?object.at(key).get<std::string>():object.at(key).dump();
    }
    static void DrawStatusIdentity(const char* label,const std::string& value) {
        const auto shortValue=value.size()>16?value.substr(0,12):value;
        ImGui::TextWrapped("%s: %s",label,shortValue.c_str());
        if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",value.c_str());
    }
    static void DrawStatusArtifact(const char* label,const Json& row) {
        const auto state=StatusText(row,"status"),fresh=StatusText(row,"freshness");
        ImGui::TextWrapped("%s: %s%s%s",label,state.c_str(),fresh==state?"":" | ",fresh==state?"":fresh.c_str());
        for(const auto& reason:row.value("reasons",Json::array()))if(reason.is_string())ImGui::TextWrapped("%s",reason.get_ref<const std::string&>().c_str());
        if(row.contains("path")&&!row.at("path").is_null()) {
            ImGui::TextDisabled("Artifact (?)");if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",StatusText(row,"path").c_str());
        }
    }
    static std::string StatusLabel(std::string key) {
        std::replace(key.begin(),key.end(),'_',' ');
        if(!key.empty())key.front()=static_cast<char>(std::toupper(static_cast<unsigned char>(key.front())));
        return key;
    }
    static void DrawStatusCapabilities(const Json& matrix) {
        if(matrix.contains("reason"))ImGui::TextWrapped("%s",StatusText(matrix,"reason","").c_str());
        const auto limits=matrix.value("limits",Json::object());
        for(const auto& row:matrix.value("capabilities",Json::array())) {
            const auto label=StatusText(row,"label",StatusText(row,"id"))+" | "+StatusText(row,"state");
            if(ImGui::TreeNode(label.c_str())) {
                ImGui::TextWrapped("%s",StatusText(row,"description","").c_str());
                for(const auto& id:row.value("limits",Json::array()))if(id.is_string()&&limits.contains(id.get<std::string>())) {
                    const auto key=id.get<std::string>();const auto& limit=limits.at(key);
                    ImGui::TextWrapped("%s: %s %s",StatusLabel(key).c_str(),StatusText(limit,"value").c_str(),StatusText(limit,"unit","").c_str());
                    if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",StatusText(limit,"scope","").c_str());
                }
                ImGui::TreePop();
            }
        }
    }
    static void DrawStatusEnvironment(const Json& environment) {
        if(environment.contains("reason"))ImGui::TextWrapped("%s",StatusText(environment,"reason","").c_str());
        const auto tools=environment.value("tools",Json::object());
        for(const auto& [name,row]:tools.items()) {
            const auto label=StatusLabel(name)+" | "+StatusText(row,"status");
            if(ImGui::TreeNode(label.c_str())) {
                ImGui::TextWrapped("Path: %s",StatusText(row,"path").c_str());
                const auto version=StatusText(row,"version","");
                if(!version.empty())ImGui::TextWrapped("Version: %s",version.substr(0,version.find('\n')).c_str());
                for(const auto& diagnostic:row.value("diagnostics",Json::array())) {
                    ImGui::TextWrapped("%s",StatusText(diagnostic,"message","").c_str());
                    ImGui::TextWrapped("%s",StatusText(diagnostic,"action","").c_str());
                }
                ImGui::TreePop();
            }
        }
        if(environment.contains("scope"))ImGui::TextWrapped("%s",StatusText(environment,"scope","").c_str());
    }
    void DrawProjectStatus() {
        if(!projectStatusVisible_) {
            projectStatusVisible_=true;
            if(!projectStatusJob_.valid())Defer([this]{if(!projectStatusJob_.valid())RefreshProjectStatus();});
        }
        if(BeginPanel("Project Status")) {
            ImGui::BeginDisabled(projectStatusJob_.valid());
            if(ImGui::Button("Refresh"))Defer([this]{if(!projectStatusJob_.valid())RefreshProjectStatus();});
            ImGui::EndDisabled();ImGui::SameLine();
            ImGui::TextDisabled(projectStatusJob_.valid()?"Checking project...":"Read-only snapshot");
            if(!projectStatusError_.empty())ImGui::TextWrapped("Status unavailable: %s",projectStatusError_.c_str());
            if(projectStatusDraftStale_)ImGui::TextWrapped("The active draft changed after this snapshot. Refresh to check it.");
            if(!projectStatus_.empty()) {
                ImGui::TextWrapped("Collected: %s",StatusText(projectStatus_,"generated_at_utc").c_str());
                const auto collection=projectStatus_.value("collection",Json::object());
                if(collection.contains("consistent")&&collection.at("consistent").is_boolean()&&!collection.at("consistent").get<bool>())
                    ImGui::TextWrapped("Files changed during this check. Refresh for a consistent snapshot.");
                const auto checkout=projectStatus_.value("checkout",Json::object());
                const bool checkoutDirty=checkout.contains("dirty")&&checkout.at("dirty").is_boolean()&&checkout.at("dirty").get<bool>();
                ImGui::Separator();ImGui::TextWrapped("Branch: %s%s",StatusText(checkout,"branch").c_str(),checkoutDirty?" | modified":"");
                DrawStatusIdentity("Commit",StatusText(checkout,"commit"));
                if(checkout.value("status",std::string())=="unavailable")ImGui::TextWrapped("%s",StatusText(checkout,"reason","").c_str());
                const auto draft=projectStatus_.value("draft",Json::object());
                ImGui::TextWrapped("Scene: %s",StatusText(draft,"scene_file",source_.filename().string()).c_str());
                ImGui::TextUnformatted(!draft.contains("dirty")||!draft.at("dirty").is_boolean()?"Scene draft identity unknown":
                    draft.at("dirty").get<bool>()?"Unsaved scene draft":"Scene matches saved source");
                DrawStatusIdentity("Saved scene",StatusText(draft,"saved_source_sha256"));
                DrawStatusIdentity("Draft scene",StatusText(draft,"source_sha256"));
                if(draft.contains("partition_file")&&!draft.at("partition_file").is_null()) {
                    ImGui::TextWrapped("Partition: %s",StatusText(draft,"partition_file").c_str());
                    ImGui::TextUnformatted(!draft.contains("partition_dirty")||!draft.at("partition_dirty").is_boolean()?"Partition draft identity unknown":
                        draft.at("partition_dirty").get<bool>()?"Unsaved partition draft":"Partition matches saved source");
                    DrawStatusIdentity("Saved partition",StatusText(draft,"saved_partition_sha256"));
                    DrawStatusIdentity("Draft partition",StatusText(draft,"partition_sha256"));
                }
                for(const auto& scene:projectStatus_.value("scenes",Json::array())) {
                    const auto file=StatusText(scene,"file","");
                    if(file!=source_.filename().string()&&file!=fs::relative(source_,root_).generic_string())continue;
                    DrawStatusIdentity("Saved scene file",StatusText(scene,"source_sha256"));
                    const auto partition=scene.value("partition",Json::object());
                    if(partition.contains("sha256")&&!partition.at("sha256").is_null())DrawStatusIdentity("Saved partition file",StatusText(partition,"sha256"));
                    ImGui::Separator();DrawStatusArtifact("Cook",scene.value("cook",Json::object()));
                    DrawStatusArtifact("ROM",scene.value("rom",Json::object()));
                }
                if(ImGui::CollapsingHeader("Reports"))for(const auto& row:projectStatus_.value("reports",Json::array())) {
                    ImGui::PushID(StatusText(row,"path",StatusText(row,"kind")).c_str());
                    DrawStatusArtifact(StatusText(row,"kind","Report").c_str(),row);ImGui::Separator();ImGui::PopID();
                }
                if(ImGui::CollapsingHeader("Sessions")) {
                    const auto sessions=projectStatus_.value("sessions",Json::array());
                    if(sessions.empty())ImGui::TextDisabled("No known sessions.");
                    for(const auto& row:sessions) {
                        ImGui::TextWrapped("%s | PID %s | alive %s | owned %s | identity %s",StatusText(row,"kind").c_str(),StatusText(row,"pid").c_str(),
                            StatusText(row,"alive").c_str(),StatusText(row,"owned").c_str(),StatusText(row,"identity_status").c_str());
                        ImGui::TextWrapped("Scene: %s",StatusText(row,"scene").c_str());
                        DrawStatusIdentity("Build",StatusText(row,"build"));DrawStatusIdentity("Package",StatusText(row,"package_sha256"));
                        for(const auto* key:{"native_play","stream_preview"})if(row.contains(key)&&row.at(key).is_object()) {
                            const auto& mode=row.at(key);
                            ImGui::TextWrapped("%s: %s | active %s",StatusLabel(key).c_str(),StatusText(mode,"mode").c_str(),StatusText(mode,"active").c_str());
                            DrawStatusIdentity("Play package",StatusText(mode,"package_sha256"));
                        }
                        if(row.contains("reason"))ImGui::TextWrapped("%s",StatusText(row,"reason","").c_str());
                    }
                }
                if(ImGui::CollapsingHeader("Environment"))DrawStatusEnvironment(projectStatus_.value("environment",Json::object()));
                if(ImGui::CollapsingHeader("Capabilities and limits"))DrawStatusCapabilities(projectStatus_.value("capabilities",Json::object()));
                if(ImGui::CollapsingHeader("Known failures and unavailable data"))DrawStatusDetails(projectStatus_.value("summary",Json::object()));
                ImGui::TextDisabled("Refresh also checks changes made outside the editor.");
            }
        }
        ImGui::End();
    }
    static void DrawStatusDetails(const Json& value) {
        if(value.is_array())for(const auto& row:value){DrawStatusDetails(row);ImGui::Separator();}
        else if(value.is_object())for(const auto& [key,row]:value.items()) {
            if(row.is_object()||row.is_array()) {if(ImGui::TreeNode(key.c_str())){DrawStatusDetails(row);ImGui::TreePop();}}
            else ImGui::TextWrapped("%s: %s",key.c_str(),row.is_string()?row.get_ref<const std::string&>().c_str():row.dump().c_str());
        }
        else ImGui::TextWrapped("%s",value.dump().c_str());
    }
