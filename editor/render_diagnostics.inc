    Json diagnosticsSnapshot_=nullptr, savedRomDiagnostics_=Json::object(),savedRomBindings_=Json::object();
    bool diagnosticsDependenciesCurrent_=false,savedRomDependenciesCurrent_=false;
    std::chrono::steady_clock::time_point diagnosticsChecked_{};
    void AdoptDiagnostics(const Json& snapshot) {
        diagnosticsSnapshot_=snapshot;diagnosticsChecked_={};
    }
    bool DiagnosticHashesCurrent(const Json& dependencies,bool mission=false) const {
        if(!dependencies.is_array())return false;
        try {for(const auto& item:dependencies) {
            const auto uri=item.value(mission?"path":"uri",std::string());
            const fs::path path=uri.starts_with("code:")?root_/uri.substr(5):source_.parent_path()/uri;
            if(DlStreamPlay::FileSha256(path.string())!=item.at("sha256").get<std::string>())return false;
        }}catch(const std::exception&){return false;}
        return true;
    }
    void CheckDiagnosticDependencies(bool force=false) {
        const auto now=std::chrono::steady_clock::now();
        if(!force&&now-diagnosticsChecked_<std::chrono::seconds(1))return;
        diagnosticsChecked_=now;
        diagnosticsDependenciesCurrent_=DiagnosticHashesCurrent(report_.value("dependencies",Json()));
        savedRomDependenciesCurrent_=false;
        if(!savedRomDiagnostics_.empty())try {
            savedRomDependenciesCurrent_=DiagnosticHashesCurrent(savedRomBindings_.at("dependencies"),savedRomBindings_.at("mission").get<bool>())&&
                DlStreamPlay::FileSha256(source_.string())==savedRomBindings_.at("source_file_sha256").get<std::string>()&&
                DlStreamPlay::FileSha256(savedRomDiagnostics_.at("rom").get<std::string>())==savedRomDiagnostics_.at("identity").at("rom_sha256").get<std::string>()&&
                DlStreamPlay::FileSha256(savedRomDiagnostics_.at("runtime_debug_map").get<std::string>())==savedRomBindings_.at("debug_sha256").get<std::string>();
            if(savedRomBindings_.at("mission").get<bool>())savedRomDependenciesCurrent_=savedRomDependenciesCurrent_&&
                DlStreamPlay::FileSha256(streamPath_.string())==savedRomBindings_.at("mission_manifest_sha256").get<std::string>();
        }catch(const std::exception&){}
    }
    Json RenderDiagnostics(bool force=false) {
        CheckDiagnosticDependencies(force);
        Json doors=Json::array();for(const auto& row:document_.at("entities"))if(row.value("kind",std::string())=="door")doors.push_back(row.at("id"));
        const bool stale=diagnosticsSnapshot_!=AuthorSnapshot()||!diagnosticsDependenciesCurrent_;
        const bool night=document_.contains("environment")&&!document_.at("environment").is_null();
        const std::string notice=night&&doors.size()>1?
            "Multiple independent door objects disable the two-state inline shortcut (maximum one door). Static night lighting uses live preparation and live door changes; recook for affected model counts.":
            night?"At most one independent door object: two-state inline lighting is eligible when complete baked colors exist; recook for model-specific paths.":
                  "Legacy environment: scalar lighting, actor probes and objective emissive paths apply.";
        Json saved=savedRomDiagnostics_;
        saved["current"]=!savedRomDiagnostics_.empty()&&savedRomDependenciesCurrent_&&!dirty_&&diagnosticsSnapshot_==AuthorSnapshot();
        saved["dependencies_current"]=savedRomDependenciesCurrent_;
        const auto bridge=report_.value("bridge_diagnostics",Json());
        const auto resource=report_.value("resource_diagnostics",Json::object());
        return {{"source",source_.string()},{"cooked_stale",stale},{"dependencies_current",diagnosticsDependenciesCurrent_},
            {"draft_door_ids",doors},{"draft_door_count",doors.size()},{"immediate_notice",notice},
            {"cooked_renderer_diagnostics",report_.value("renderer_diagnostics",Json())},
            {"capacity_diagnostics",bridge},{"memory",bridge.is_object()?bridge.value("memory",Json()):resource.value("memory",Json())},
            {"saved_rom",saved}};
    }
    void FocusDiagnosticBounds(glm::vec3 lower,glm::vec3 upper) {
        if(!scene_||!scene_->cameraId)return;
        const auto id=*scene_->cameraId;if(!scene_->cameras[id])return;
        auto& camera=*scene_->cameras[id];auto& transform=scene_->transforms[id];
        const auto target=(lower+upper)*.5f;
        const float radius=std::max(.5f,glm::length(upper-lower)*.5f);
        const float distance=radius/std::sin(glm::radians(std::clamp(camera.fov,20.f,100.f))*.5f)*1.15f;
        transform.position=target+glm::normalize(glm::vec3(.38f,.35f,1.f))*distance;
        transform.rotation=glm::quatLookAt(glm::normalize(target-transform.position),glm::vec3(0,1,0));camera.orbitDistance=distance;
    }
    Json SelectResourceWitness(const Json& args) {
        if(engine_.IsPlaying()||Busy())throw std::runtime_error("Finish Play/build before selecting a resource witness");
        const std::string kind=args.at("kind"),id=args.at("id");
        const auto diagnostics=RenderDiagnostics(true);Json ids=Json::array();
        if(kind=="region"||kind=="transition") {
            SelectStreamRow(kind=="region"?"region":"gate",id);
            const auto* row=SelectedStreamRow();if(!row)throw std::runtime_error("Resource source row no longer exists");
            float lo[3],hi[3];if(ReadStreamBounds(*row,lo,hi))FocusDiagnosticBounds({lo[0],lo[1],lo[2]},{hi[0],hi[1],hi[2]});
            ShowPanel("Streaming");
        }else if(kind=="object"||kind=="group") {
            if(kind=="object")ids.push_back(id);
            else {
                if(diagnostics.at("cooked_stale"))throw std::runtime_error("Cooked group witness is stale; refresh diagnostics before selecting its contributors");
                if(args.contains("expected_package_sha256")&&args.at("expected_package_sha256")!=diagnostics.at("cooked_renderer_diagnostics").at("identity").at("package_sha256"))
                    throw std::runtime_error("Cooked group witness belongs to another package; inspect the current report");
                const auto capacity=diagnostics.at("capacity_diagnostics");
                if(capacity.is_object())for(const auto& working:capacity.value("working_sets",Json::array()))
                    for(const auto& group:working.value("groups",Json::array()))if(group.at("id")==id)ids=group.at("source_entity_ids");
                if(ids.empty())throw std::runtime_error("Unknown cooked group witness");
            }
            for(const auto& sourceId:ids)if(!Entity(sourceId.get<std::string>()))throw std::runtime_error("Resource object no longer exists");
            StopStreamPreview();ClearStreamBoundsHandle();AuthorCommand("select_entities",{{"ids",ids}});
            glm::vec3 lo(std::numeric_limits<float>::max()),hi(-std::numeric_limits<float>::max());
            for(const auto& sourceId:ids){const auto p=Vector(Entity(sourceId.get<std::string>())->at("transform").at("position"));lo=glm::min(lo,p);hi=glm::max(hi,p);}
            FocusDiagnosticBounds(lo-glm::vec3(1),hi+glm::vec3(1));ShowPanel("Inspector");ShowPanel("Objects");
        }else throw std::runtime_error("Choose region, transition, object or group witness");
        return {{"kind",kind},{"id",id},{"selected_ids",ids},{"selected_region",streamPreviewSelectedRegion_},
            {"selected_transition",streamPreviewSelectedGate_},{"diagnostics",diagnostics}};
    }
    void DrawResourceLink(const char* kind,const std::string& id,bool enabled=true) {
        ImGui::BeginDisabled(!enabled||Busy()||engine_.IsPlaying());
        if(ImGui::SmallButton((std::string(kind)+": "+id).c_str()))Defer([this,kind=std::string(kind),id]{SelectResourceWitness({{"kind",kind},{"id",id}});});
        ImGui::EndDisabled();
    }
    void DrawPolicyCases(const Json& policy) {
        if(!policy.is_object()){ImGui::TextDisabled("Lighting policy unavailable; refresh diagnostics.");return;}
        for(const char* phase:{"cases","change_cases"}) {
            ImGui::TextUnformatted(std::string(phase)=="cases"?"Scene preparation / publication":"After a door state changes");
            for(const auto& row:policy.value(phase,Json::array()))ImGui::TextWrapped("%s: %u models, %u triangles (%s)",
                row.at("mode").get<std::string>().c_str(),row.value("models",0u),row.value("triangles",0u),row.at("reason").get<std::string>().c_str());
        }
        ImGui::TextWrapped("%s",policy.value("scope",std::string("Shared target policy on published models; desktop rendering and frame time are separate measurements.")).c_str());
    }
    void DrawWitness(const Json& witness,bool current) {
        const std::string anchor=witness.value("anchor",std::string());if(!anchor.empty())DrawResourceLink("region",anchor);
        for(const auto& gate:witness.value("gates",Json::array()))DrawResourceLink("transition",gate.get<std::string>());
        if(ImGui::TreeNode("Constituent regions, groups and objects")) {
            for(const auto& region:witness.value("source_regions",Json::array()))DrawResourceLink("region",region.get<std::string>());
            for(const auto& group:witness.value("groups",Json::array())){ImGui::PushID(group.at("id").get<std::string>().c_str());
                DrawResourceLink("group",group.at("id"),current);ImGui::Text("%u static triangles",group.value("triangles",0u));
                for(const auto& entity:group.value("source_entity_ids",Json::array()))DrawResourceLink("object",entity.get<std::string>());ImGui::PopID();}
            for(const auto& entity:witness.value("contributors",Json::array()))DrawResourceLink("object",entity.at("id").get<std::string>());
            ImGui::TreePop();
        }
    }
    void DrawRenderDiagnostics() {
        const auto state=RenderDiagnostics();const bool current=!state.at("cooked_stale").get<bool>();
        ImGui::SeparatorText("Rendering paths and resource witnesses");
        ImGui::TextWrapped("%s",state.at("immediate_notice").get<std::string>().c_str());
        for(const auto& id:state.at("draft_door_ids"))DrawResourceLink("object",id.get<std::string>());
        ImGui::BeginDisabled(Busy()||engine_.IsPlaying());
        if(ImGui::Button("Refresh draft diagnostics"))Defer([this]{RefreshDraft();});ImGui::EndDisabled();
        ImGui::TextColored(current?ImVec4(.3f,1,.5f,1):ImVec4(1,.5f,.1f,1),"%s",current?"Current cooked draft prediction":"STALE: draft or dependency changed; refresh before relying on counts");
        if(ImGui::CollapsingHeader("Cooked lighting paths")) {
            DrawPolicyCases(state.at("cooked_renderer_diagnostics"));
            const auto policy=state.at("cooked_renderer_diagnostics");if(policy.is_object()){
                ImGui::TextWrapped("Identity: %s",policy.at("identity").dump().c_str());
                for(const auto& row:policy.value("model_entries",Json::array()))if(ImGui::TreeNode(row.value("group_id",row.value("entity_id",std::string("model"))).c_str())) {
                    ImGui::TextWrapped("%s (%s); changes: %s (%s)",row.at("mode").get<std::string>().c_str(),row.at("reason").get<std::string>().c_str(),row.at("change_mode").get<std::string>().c_str(),row.at("change_reason").get<std::string>().c_str());
                    for(const auto& id:row.value("source_entity_ids",Json::array()))DrawResourceLink("object",id.get<std::string>());ImGui::TreePop();}
            }
        }
        const auto capacity=state.at("capacity_diagnostics");
        if(capacity.is_object()&&ImGui::CollapsingHeader("Working-set capacities")) {
            ImGui::TextWrapped("Cooked estimates: %s. Invalid target drafts remain editable.",capacity.value("valid",false)?"within reported adapter caps":"target capacity failure");
            int index=0;for(const auto& failure:capacity.value("failures",Json::array())) {ImGui::PushID(index++);
                ImGui::TextWrapped("%s: %s / %s. %s",failure.at("metric").get<std::string>().c_str(),failure.at("usage").dump().c_str(),failure.at("cap").dump().c_str(),failure.at("message").get<std::string>().c_str());DrawWitness(failure,current);ImGui::PopID();}
            for(const auto& [metric,row]:capacity.at("maxima").items())if(ImGui::TreeNode(metric.c_str(),"%s maximum: %s / %s",metric.c_str(),row.at("usage").dump().c_str(),row.at("cap").is_null()?"unknown / uncapped":row.at("cap").dump().c_str())) {
                index=0;for(const auto& witness:row.at("witnesses")){ImGui::PushID(index++);DrawWitness(witness,current);ImGui::PopID();}ImGui::TreePop();}
        }
        if(ImGui::CollapsingHeader("Storage and memory estimates")) {
            const auto memory=state.at("memory");if(memory.is_object()){
                for(const auto& [key,value]:memory.items())if(value.is_number()||value.is_null())ImGui::TextWrapped("%s: %s",key.c_str(),value.is_null()?"unknown":value.dump().c_str());
                ImGui::TextWrapped("%s",memory.value("accounting_note",std::string()).c_str());
                for(const auto& cost:memory.value("unknown_target_costs",Json::array()))ImGui::TextWrapped("Unknown: %s",cost.get<std::string>().c_str());
                ImGui::TextWrapped("Embedded RGBA is a subset of package storage. Host worker cache is excluded from target totals. These estimates do not certify total RDRAM or frame time.");
            }
        }
        if(StreamPreviewActive()&&ImGui::CollapsingHeader("Published runtime preview policy"))DrawPolicyCases(StreamPreviewState().value("renderer_runtime",Json()));
        if(ImGui::CollapsingHeader("Saved ROM identity and paths")) {
            if(ImGui::Button("Refresh saved ROM report")){RefreshStreamCookReport();CheckDiagnosticDependencies(true);}
            const auto saved=state.at("saved_rom");ImGui::TextWrapped("%s",saved.value("current",false)?"Current saved-source ROM build":"Unavailable or stale: build the current saved source for exact ROM paths");
            if(saved.contains("identity"))ImGui::TextWrapped("%s",saved.at("identity").dump().c_str());
            DrawPolicyCases(saved.value("renderer_diagnostics",Json()));
            ImGui::TextWrapped("Runtime measured heap and timing are recorded by runtime inspection and performance evidence. They overlap some estimates and must not be added to the known-array subtotal.");
        }
    }
