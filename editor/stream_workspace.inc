    bool streamOverlays_=true;
    std::string streamSelectionKind_,streamSelectionId_;
    std::string lastStreamObjectSelection_;
    std::optional<EntityId> streamBoundsHandle_;
    std::string streamHandleField_="bounds",streamHandleEnd_="center";
    Json streamHandleBounds_;
    glm::vec3 streamHandleOrigin_{};
    char regionNewId_[65]="new-region",regionRenameId_[65]="renamed-region",reverseGateId_[65]="return-gate";
    std::string regionReassign_;
    bool pairedGate_=true,previewAutomatic_=false;
    int previewReadDelay_=0,previewReadSlice_=256,previewRendererDelay_=0,previewRspDelay_=0,previewRdpDelay_=0;

    std::string ObjectRegionHome(const std::string& id) const {
        if(!streamDraft_.is_object()||!streamDraft_.contains("regions")||!streamDraft_.at("regions").is_array())return {};
        std::string home;
        for(const auto& row:streamDraft_.at("regions"))if(row.is_object()&&row.contains("entities")&&row.at("entities").is_array())
            for(const auto& owned:row.at("entities"))if(owned==id){if(!home.empty())return "MULTIPLE HOMES";home=StreamText(row,"id");}
        return home;
    }
    const Json* SelectedStreamRow() const {
        if(!streamDraft_.is_object())return nullptr;
        const char* key=streamSelectionKind_=="gate"?"transitions":"regions";
        if(!streamDraft_.contains(key)||!streamDraft_.at(key).is_array())return nullptr;
        for(const auto& row:streamDraft_.at(key))if(StreamText(row,"id")==streamSelectionId_)return &row;
        return nullptr;
    }
    void ClearStreamBoundsHandle() {
        if(streamBoundsHandle_&&scene_&&*streamBoundsHandle_<scene_->alive.size())scene_->DeleteEntity(*streamBoundsHandle_);
        streamBoundsHandle_.reset();streamHandleBounds_=nullptr;
    }
    Json StreamBoundsState() const {
        Json state={{"kind",streamSelectionKind_},{"id",streamSelectionId_},{"field",streamHandleField_},{"handle",streamHandleEnd_},{"active",streamBoundsHandle_.has_value()}};
        if(streamBoundsHandle_&&scene_&&*streamBoundsHandle_<scene_->transforms.size()){
            const auto p=scene_->transforms[*streamBoundsHandle_].position;state["position"]={p.x,p.y,p.z};state["entity_id"]=*streamBoundsHandle_;
        }
        return state;
    }
    void SelectStreamBounds(const Json& args) {
        if(engine_.IsPlaying()||Busy())throw std::runtime_error("Finish Play/build before dragging stream bounds");
        const auto kind=args.value("kind",std::string("region"));
        const auto field=args.value("field",std::string("bounds")),end=args.value("handle",std::string("center"));
        if((kind!="region"&&kind!="gate")||(field!="bounds"&&field!="activation_bounds")||(kind=="gate"&&field!="bounds")||
            (end!="minimum"&&end!="maximum"&&end!="center"))throw std::runtime_error("Choose region/gate bounds and minimum, maximum or center handle");
        const auto id=args.at("id").get<std::string>();
        const auto oldKind=streamSelectionKind_,oldId=streamSelectionId_;
        streamSelectionKind_=kind;streamSelectionId_=id;
        const auto* row=SelectedStreamRow();float lo[3],hi[3];
        if(!row||!row->contains(field)||!ReadStreamBounds(Json{{"bounds",row->at(field)}},lo,hi)){
            streamSelectionKind_=oldKind;streamSelectionId_=oldId;throw std::runtime_error("Select existing finite stream bounds");
        }
        ClearStreamBoundsHandle();streamHandleField_=field;streamHandleEnd_=end;streamHandleBounds_=row->at(field);
        const auto proxy=scene_->NewEntity("Streaming bounds handle");streamBoundsHandle_=proxy.id;scene_->isImported[proxy.id]=true;
        auto& transform=scene_->transforms[proxy.id];
        transform.position=end=="minimum"?glm::vec3(lo[0],lo[1],lo[2]):end=="maximum"?glm::vec3(hi[0],hi[1],hi[2]):glm::vec3((lo[0]+hi[0])*.5f,(lo[1]+hi[1])*.5f,(lo[2]+hi[2])*.5f);
        streamHandleOrigin_=transform.position;engine_.SetEditorTransformMode(TransformMode::Translate);engine_.SetEditorSelection(proxy.id);
        StreamPreviewCommand("select_stream_preview",{{kind=="gate"?"transition":"region",id}});
    }
    Json MovedStreamBounds() const {
        Json bounds=streamHandleBounds_;if(!streamBoundsHandle_||!scene_)return bounds;
        const auto p=scene_->transforms[*streamBoundsHandle_].position;
        for(int axis=0;axis<3;++axis) {
            if(streamHandleEnd_=="center")for(int end=0;end<2;++end)bounds[end][axis]=bounds[end][axis].get<float>()+p[axis]-streamHandleOrigin_[axis];
            else bounds[streamHandleEnd_=="minimum"?0:1][axis]=p[axis];
        }
        return bounds;
    }
    void SyncStreamBoundsHandle(bool force=false) {
        if(!streamBoundsHandle_||!scene_||*streamBoundsHandle_>=scene_->alive.size())return;
        const auto* row=SelectedStreamRow();
        if(!row||!row->contains(streamHandleField_)||row->at(streamHandleField_)!=streamHandleBounds_){ClearStreamBoundsHandle();return;}
        if(!force&&ImGui::GetCurrentContext()&&ImGuizmo::IsUsing())return;
        if(glm::length(scene_->transforms[*streamBoundsHandle_].position-streamHandleOrigin_)<.00001f)return;
        const auto value=MovedStreamBounds();
        try {
            StreamCommand(streamSelectionKind_=="gate"?"set_transition":"set_region",{{"id",streamSelectionId_},{"patch",{{streamHandleField_,value}}}});
            streamHandleBounds_=value;streamHandleOrigin_=scene_->transforms[*streamBoundsHandle_].position;
        }catch(const std::exception& e){scene_->transforms[*streamBoundsHandle_].position=streamHandleOrigin_;status_=e.what();}
    }
    Json StreamBoundsCommand(const std::string& op,const Json& args) {
        if(op=="select_stream_bounds")SelectStreamBounds(args);
        else if(op=="clear_stream_bounds")ClearStreamBoundsHandle();
        else if(op=="move_stream_bounds") {
            if(engine_.IsPlaying()||Busy()||!streamBoundsHandle_)throw std::runtime_error("Select a stream bounds handle while editing");
            auto& p=scene_->transforms[*streamBoundsHandle_].position;const auto previous=p;
            if(args.contains("position")){CheckVector(args.at("position"),"position",-1024,1024);p=Vector(args.at("position"));}
            else {CheckVector(args.at("delta"),"delta",-1024,1024);p+=Vector(args.at("delta"));}
            // This is the same release path used by the viewport translation gizmo.
            const auto desired=MovedStreamBounds();
            try{StreamCommand(streamSelectionKind_=="gate"?"set_transition":"set_region",{{"id",streamSelectionId_},{"patch",{{streamHandleField_,desired}}}});}
            catch(...){p=previous;throw;}
            streamHandleBounds_=desired;streamHandleOrigin_=p;
        }
        return StreamBoundsState();
    }
    void SelectStreamRow(const std::string& kind,const std::string& id) {
        ClearStreamBoundsHandle();streamSelectionKind_=kind;streamSelectionId_=id;
        StreamPreviewCommand("select_stream_preview",{{kind=="gate"?"transition":"region",id}});
    }
    void UpdateStreamObjectSelection() {
        if(lastStreamObjectSelection_==selected_)return;
        lastStreamObjectSelection_=selected_;
        if(!streamDraft_.is_object()||!streamDraft_.contains("transitions")||!streamDraft_.at("transitions").is_array())return;
        for(const auto& gate:streamDraft_.at("transitions"))if(StreamText(gate,"door")==selected_&&!selected_.empty()){
            SelectStreamRow("gate",StreamText(gate,"id"));return;
        }
    }
    void DrawStreamHandles(const std::string& kind,const std::string& id,const std::string& field) {
        ImGui::PushID((kind+id+field).c_str());
        if(ImGui::SmallButton(("Select "+field).c_str()))Defer([this,kind,id]{SelectStreamRow(kind,id);});
        for(const char* end:{"minimum","maximum","center"}){
            ImGui::SameLine();if(ImGui::SmallButton(end))Defer([this,kind,id,field,end]{SelectStreamBounds({{"kind",kind},{"id",id},{"field",field},{"handle",end}});});
        }
        ImGui::PopID();
    }
    void DrawStreamCapacity(const Json& capacity,const char* label) {
        if(!capacity.is_object()){ImGui::TextDisabled("%s capacity: validate or recook to obtain a witness",label);return;}
        ImGui::Text("%s capacity witness",label);
        ImGui::Text("Regions %u / 5 | triangles %u / 1440 | groups %u / 64",unsigned(capacity.value("regions",Json::array()).size()),capacity.value("triangle_count",0u),capacity.value("group_count",0u));
        ImGui::Text("Boxes + floors + barriers %u / 400 | floors %u / 128",capacity.value("collider_count",0u)+capacity.value("floor_count",0u)+capacity.value("barrier_count",0u),capacity.value("floor_count",0u));
        ImGui::Text("Models %u / 128 | decoded chunks %u bytes",capacity.value("model_count",0u),capacity.value("chunk_bytes",0u));
    }
    void DrawStreamPreviewControls() {
        const auto preview=StreamPreviewState();
        ImGui::SeparatorText(StreamPreviewActive()?"Cooked runtime preview":"Full authoring view");
        ImGui::TextWrapped("Authoring shows all source objects. Runtime preview shows only the complete published cooked bank.");
        ImGui::BeginDisabled(engine_.IsPlaying()||Busy());
        if(ImGui::Button(StreamPreviewActive()?"Recook runtime preview":"Cook runtime preview"))Defer([this]{StreamPreviewCommand("start_stream_preview",{{"automatic",previewAutomatic_}});});
        if(StreamPreviewActive()){
            ImGui::SameLine();if(ImGui::Button("Full authoring view"))Defer([this]{StopStreamPreview();});
            if(ImGui::Button("Step preview"))Defer([this]{StreamPreviewCommand("step_stream_preview",{{"ticks",1}});});
        }
        if(ImGui::Checkbox("Live preview camera",&previewAutomatic_)&&StreamPreviewActive())Defer([this]{StreamPreviewCommand("set_stream_preview_transport",{{"automatic",previewAutomatic_}});});
        ImGui::EndDisabled();ImGui::Checkbox("Streaming overlays",&streamOverlays_);
        if(StreamPreviewActive()){
            ImGui::TextColored(preview.value("stale_draft",false)?ImVec4(1,.5f,.1f,1):ImVec4(.3f,1,.5f,1),"%s",preview.value("stale_draft",false)?"STALE DRAFT - recook to update":"Current cooked snapshot");
            const auto identity=preview.value("identity",Json::object());
            for(const auto& entry:std::vector<std::pair<std::string,std::string>>{{"Cooked source","cooked_source_sha256"},{"Cooked partition","cooked_partition_sha256"},{"Mission package","package_sha256"},{"Play snapshot","play_fingerprint"}}){
                const auto hash=identity.value(entry.second,std::string());ImGui::Text("%s: %.12s",entry.first.c_str(),hash.c_str());
                if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",hash.c_str());
            }
            const auto stream=preview.at("stream");
            ImGui::Text("Published 0x%X | physical 0x%X | pending 0x%X",stream.value("published_mask",0u),stream.value("physical_mask",0u),stream.value("pending_mask",0u));
            ImGui::Text("Published geometry: %u triangles",preview.value("rendered_triangles",0u));
            if(preview.value("camera_outside_regions",false))ImGui::TextWrapped("Camera is outside authored regions. The preview keeps its last valid visibility anchor until re-entry.");
            if(stream.value("preparing",false))ImGui::TextWrapped("Preparing candidate: CPU %s, renderer %s. Published view stays complete.",stream.value("cpu_ready",false)?"ready":"pending",stream.value("gpu_ready",false)?"ready":"pending");
            if(!preview.value("error",std::string()).empty())ImGui::TextWrapped("Preview: %s",preview.at("error").get<std::string>().c_str());
        }
        const auto selection=preview.value("selection",Json::object());
        if(!selection.value("region",std::string()).empty()) {
            ImGui::SeparatorText("Selected visibility");
            ImGui::TextWrapped("Region %s | gate %s",selection.value("region",std::string()).c_str(),selection.value("transition",std::string()).c_str());
            for(const auto& entry:std::vector<std::pair<std::string,std::string>>{{"Stable visible set","stable_set"},{"Destination visible set","destination_set"},{"Transition union","transition_union"}})ImGui::TextWrapped("%s: %s",entry.first.c_str(),selection.value(entry.second,Json::array()).dump().c_str());
            DrawStreamCapacity(selection.value("stable_capacity",Json()),"Stable set");
            if(!selection.value("transition",std::string()).empty())DrawStreamCapacity(selection.value("union_capacity",Json()),"Transition union");
            if(selection.value("transition",std::string()).empty())for(const auto& outgoing:selection.value("outgoing",Json::array())){
                const auto id=outgoing.value("transition",std::string());ImGui::PushID(id.c_str());
                ImGui::TextWrapped("Opening %s -> %s",id.c_str(),outgoing.value("destination_region",std::string()).c_str());
                ImGui::TextWrapped("Destination: %s | transition union: %s",outgoing.value("destination_set",Json::array()).dump().c_str(),outgoing.value("transition_union",Json::array()).dump().c_str());
                DrawStreamCapacity(outgoing.value("union_capacity",Json()),"Opening union");
                if(ImGui::SmallButton("Select this opening"))Defer([this,id]{SelectStreamRow("gate",id);});
                ImGui::PopID();
            }
            if(selection.value("witness_stale",false))ImGui::TextWrapped("Capacity witness belongs to the previous cook; validate or recook this draft.");
        }
        if(ImGui::TreeNode("Delayed-load controls")) {
            ImGui::InputInt("Read delay (ticks)",&previewReadDelay_);ImGui::InputInt("Read slice (bytes)",&previewReadSlice_);
            ImGui::InputInt("Renderer delay (ticks)",&previewRendererDelay_);ImGui::InputInt("RSP delay (ticks)",&previewRspDelay_);ImGui::InputInt("RDP delay (ticks)",&previewRdpDelay_);
            ImGui::BeginDisabled(!StreamPreviewActive()||Busy());
            if(ImGui::Button("Apply preview transport"))Defer([this]{StreamPreviewCommand("set_stream_preview_transport",{{"read_delay_ticks",previewReadDelay_},{"read_slice_bytes",previewReadSlice_},{"renderer_delay_ticks",previewRendererDelay_},{"rsp_delay_ticks",previewRspDelay_},{"rdp_delay_ticks",previewRdpDelay_}});});
            ImGui::EndDisabled();ImGui::TreePop();
        }
        ImGui::TextColored(ImVec4(.5f,.7f,1,1),"Blue: geometry ownership | cyan: activation");
        ImGui::TextColored(ImVec4(1,.55f,.1f,1),"Orange: gates | yellow: pending loads");
        ImGui::TextColored(ImVec4(.2f,1,.45f,1),"Green: published | purple: physical residency");
    }
    void DrawRegionLifecycle(const Json& regions) {
        ImGui::SeparatorText("Region authoring");ImGui::InputText("New region ID",regionNewId_,sizeof regionNewId_);
        if(ImGui::Button("Create region")){
            const std::string id=regionNewId_;Defer([this,id]{StreamCommand("create_region",{{"id",id},{"bounds",{{-1,-1,-1},{1,3,1}}}});SelectStreamRow("region",id);});
        }
        ImGui::TextWrapped("New and duplicated regions start empty. Assign objects and fit bounds before cooking.");
        if(streamSelectionKind_=="region"&&SelectedStreamRow()) {
            ImGui::Text("Selected region: %s",streamSelectionId_.c_str());ImGui::InputText("Replacement ID",regionRenameId_,sizeof regionRenameId_);
            if(ImGui::Button("Rename region")){const auto id=streamSelectionId_,next=std::string(regionRenameId_);Defer([this,id,next]{StreamCommand("rename_region",{{"id",id},{"new_id",next}});SelectStreamRow("region",next);});}
            ImGui::SameLine();if(ImGui::Button("Duplicate region")){const auto id=streamSelectionId_,next=std::string(regionNewId_);Defer([this,id,next]{StreamCommand("duplicate_region",{{"id",id},{"new_id",next}});SelectStreamRow("region",next);});}
            if(ImGui::BeginCombo("Reassign owned objects to",regionReassign_.empty()?"Choose destination":regionReassign_.c_str())){
                for(const auto& row:regions){const auto id=StreamText(row,"id");if(id!=streamSelectionId_&&!id.empty()&&ImGui::Selectable(id.c_str(),id==regionReassign_))regionReassign_=id;}
                ImGui::EndCombo();
            }
            if(ImGui::Button("Delete region..."))ImGui::OpenPopup("Delete selected region");
            if(ImGui::BeginPopupModal("Delete selected region",nullptr,ImGuiWindowFlags_AlwaysAutoResize)){
                ImGui::TextWrapped("Delete %s and repair its gates and visible sets. Owned objects require explicit reassignment.",streamSelectionId_.c_str());
                ImGui::Text("Reassign to: %s",regionReassign_.empty()?"none selected":regionReassign_.c_str());
                if(ImGui::Button("Delete and repair")){const auto id=streamSelectionId_,target=regionReassign_;Defer([this,id,target]{Json args={{"id",id}};if(!target.empty())args["reassign_to"]=target;StreamCommand("delete_region",args);ClearStreamBoundsHandle();streamSelectionId_.clear();});ImGui::CloseCurrentPopup();}
                ImGui::SameLine();if(ImGui::Button("Cancel"))ImGui::CloseCurrentPopup();ImGui::EndPopup();
            }
        }
        const auto summary=StreamCommand("get_streaming",Json::object());
        if(summary.contains("homes")){
            const auto& homes=summary.at("homes");
            const auto unassigned=homes.value("unassigned",Json::array()),multiple=homes.value("multiply_owned",Json::array()),missing=homes.value("missing_entities",Json::array());
            ImGui::Text("Home assignments: %u unresolved, %u duplicate, %u missing",unsigned(unassigned.size()),unsigned(multiple.size()),unsigned(missing.size()));
            if(!unassigned.empty())ImGui::TextColored(ImVec4(1,.3f,.2f,1),"Assign before cooking: %s",unassigned.dump().c_str());
        }
    }
    void DrawStreamOverlays(Scene& scene,DebugRenderer& renderer) {
        if(!streamOverlays_||!streamDraft_.is_object())return;
        const auto box=[&](const Json& bounds,glm::vec4 color,float offset){float lo[3],hi[3];if(!ReadStreamBounds(Json{{"bounds",bounds}},lo,hi))return;
            glm::vec3 points[8];for(int k=0;k<8;++k)points[k]={k&1?hi[0]+offset:lo[0]-offset,k&2?hi[1]+offset:lo[1]-offset,k&4?hi[2]+offset:lo[2]-offset};
            for(int k=0;k<8;++k)for(int bit:{1,2,4})if(!(k&bit))renderer.DrawDebugLine(points[k],points[k|bit],color);
        };
        const auto preview=StreamPreviewState();const auto state=preview.value("stream",Json::object());
        if(streamDraft_.contains("regions")&&streamDraft_.at("regions").is_array()){
            for(const auto& row:streamDraft_.at("regions"))if(StreamRegion(row)){
                const auto bounds=row.value("bounds",Json());box(bounds,{.35f,.55f,1,1},0);
                if(row.contains("activation_bounds"))box(row.at("activation_bounds"),{.1f,.9f,1,1},.01f);
            }
        }
        uint32_t physical=state.value("physical_mask",0u);
        for(const auto& slot:state.value("slots",Json::array()))if(slot.value("state",0u)==DL_STREAM_RETIRING){const auto id=slot.value("region",0u);if(id>0&&id<=32)physical|=uint32_t(1)<<(id-1);}
        const uint32_t published=state.value("published_mask",0u),pending=(state.value("demand_mask",0u)|state.value("pending_mask",0u))&~published;
        // Residency belongs to the frozen cooked identity, even if draft IDs
        // have since been renamed or inserted.
        for(const auto& row:preview.value("regions",Json::array())){
            const auto id=row.value("id",0u);if(id<1||id>32)continue;const auto bit=uint32_t(1)<<(id-1);const auto bounds=row.value("bounds",Json());
            if(physical&bit)box(bounds,{.8f,.3f,1,1},.04f);if(published&bit)box(bounds,{.2f,1,.45f,1},.07f);
            if(pending&bit)box(bounds,{1,.95f,.1f,1},.1f);
        }
        if(streamDraft_.contains("transitions")&&streamDraft_.at("transitions").is_array())for(const auto& row:streamDraft_.at("transitions"))if(row.contains("bounds"))box(row.at("bounds"),{1,.5f,.1f,1},0);
        if(streamBoundsHandle_&&streamHandleBounds_.is_array()){box(MovedStreamBounds(),{1,1,1,1},.015f);renderer.DrawDebugWireSphere(scene.transforms[*streamBoundsHandle_].position,.12f,{1,1,1,1},12);}
        // Unassigned author objects are conspicuous even after bulk duplication/import.
        if(!StreamPreviewActive())for(const auto& [entity,id]:previewIds_)if(ObjectRegionHome(id).empty()||ObjectRegionHome(id)=="MULTIPLE HOMES")
            renderer.DrawDebugWireSphere(scene.transforms[entity].position,.25f,{1,.2f,.2f,1},12);
    }
    void DrawStreamViewportStatus() {
        if(!StreamingInfo())return;
        auto* window=ImGui::FindWindowByName("Viewport");const auto& viewScene=engine_.GetActiveScene();
        if(!window||!window->Active||!window->DockTabIsVisible||!viewScene.cameraId)return;
        const auto rect=window->InnerRect;auto* draw=window->DrawList;draw->PushClipRect(rect.Min,rect.Max,true);
        const auto preview=StreamPreviewState();const bool active=StreamPreviewActive(),stale=preview.value("stale_draft",false);
        const char* title=engine_.IsPlaying()?"NATIVE PLAY | PUBLISHED BANK":active?(stale?"COOKED RUNTIME PREVIEW | STALE DRAFT":"COOKED RUNTIME PREVIEW | PUBLISHED BANK"):"FULL AUTHORING VIEW | ALL SOURCE OBJECTS";
        const ImVec2 origin(rect.Min.x+10,rect.Min.y+10);const auto size=ImGui::CalcTextSize(title);
        draw->AddRectFilled(ImVec2(origin.x-4,origin.y-3),ImVec2(origin.x+size.x+4,origin.y+size.y+3),IM_COL32(12,15,23,230));draw->AddText(origin,stale?IM_COL32(255,160,30,255):IM_COL32(230,240,255,255),title);
        if(streamOverlays_&&streamDraft_.contains("regions")&&streamDraft_.at("regions").is_array()){
            const auto cameraId=*viewScene.cameraId;const auto vp=ComposeProjectionMatrix(*viewScene.cameras[cameraId],rect.GetWidth()/rect.GetHeight())*ComposeViewMatrix(viewScene.transforms[cameraId]);
            const auto label=[&](const Json& row,const std::string& kind){float lo[3],hi[3];if(!ReadStreamBounds(row,lo,hi))return;
                auto clip=vp*glm::vec4((lo[0]+hi[0])*.5f,(lo[1]+hi[1])*.5f,(lo[2]+hi[2])*.5f,1);if(clip.w<=0)return;
                const ImVec2 p(rect.Min.x+(clip.x/clip.w+1)*.5f*rect.GetWidth(),rect.Min.y+(clip.y/clip.w+1)*.5f*rect.GetHeight());
                const auto id=StreamText(row,"id"),text=kind+": "+id;const auto size=ImGui::CalcTextSize(text.c_str());
                if(!rect.Contains(p))return;draw->AddRectFilled(ImVec2(p.x-3,p.y-2),ImVec2(p.x+size.x+3,p.y+size.y+2),IM_COL32(10,15,25,215));
                draw->AddText(p,kind=="gate"?IM_COL32(255,150,50,255):IM_COL32(135,185,255,255),text.c_str());
                if(!engine_.IsPlaying()&&!Busy()&&!ImGuizmo::IsUsing()&&ImGui::IsMouseClicked(0)&&ImRect(p,ImVec2(p.x+size.x,p.y+size.y)).Contains(ImGui::GetIO().MousePos))Defer([this,kind,id]{SelectStreamRow(kind,id);ShowPanel("Streaming");});
            };
            for(const auto& row:streamDraft_.at("regions"))if(StreamRegion(row))label(row,"region");
            if(streamDraft_.contains("transitions")&&streamDraft_.at("transitions").is_array())for(const auto& row:streamDraft_.at("transitions"))if(StreamRegion(row))label(row,"gate");
        }
        draw->PopClipRect();
    }
