    // A separate cooked session leaves paired authoring/history editable. The
    // viewport borrows only the published bank and copies it into one bounded,
    // fixed-topology desktop mesh; pending banks are never submitted to it.
    std::unique_ptr<DlPlaySession> streamPreview_;
    Json streamPreviewSnapshot_=Json::object(),streamPreviewReport_=Json::object();
    Json streamPreviewRendered_=Json::array();
    std::string streamPreviewSource_,streamPreviewSourceSha_,streamPreviewPartitionSha_,streamPreviewPartitionCookSha_,streamPreviewError_;
    std::string streamPreviewSelectedRegion_,streamPreviewSelectedGate_;
    bool streamPreviewAutomatic_=false;
    bool streamPreviewCameraOutside_=false;
    DlVec3 streamPreviewDemandPosition_{};
    Scene* streamPreviewScene_=nullptr;
    std::map<EntityId,std::optional<MeshRenderer>> streamPreviewRenderers_;
    std::optional<EntityId> streamPreviewEntity_;
    std::optional<Transform> streamPreviewAuthorCamera_;
    std::optional<MeshHandle> streamPreviewMesh_;
    std::vector<Vertex> streamPreviewVertices_;
    DlGame streamPreviewRenderedGame_{};
    uint32_t streamPreviewRenderedRevision_=0;
    bool streamPreviewMeshValid_=false;
    double streamPreviewAccumulator_=0;
    static constexpr size_t streamPreviewTriangleCapacity_=4096;
    bool StreamPreviewActive() const {return streamPreview_!=nullptr;}
    static const Json& StreamPreviewRows(const Json& source,const char* field) {
        static const Json empty=Json::array();
        return source.is_object()&&source.contains(field)&&source.at(field).is_array()?source.at(field):empty;
    }
    Json StreamPreviewSelection() const {
        const bool stale=streamPreview_&&AuthorSnapshot()!=streamPreviewSnapshot_;
        Json result={{"region",streamPreviewSelectedRegion_},{"transition",streamPreviewSelectedGate_},
            {"stable_set",Json::array()},{"destination_set",Json::array()},{"transition_union",Json::array()},
            {"outgoing",Json::array()},
            {"stable_capacity",nullptr},{"union_capacity",nullptr},{"witness_stale",stale},
            {"draft_policy_valid",false},{"capacity_status",stale?"stale-draft":"unavailable"}};
        if(!streamDraft_.is_object())return result;
        const auto& regions=StreamPreviewRows(streamDraft_,"regions");const auto& gates=StreamPreviewRows(streamDraft_,"transitions");
        bool valid=streamDraft_.contains("regions")&&streamDraft_.at("regions").is_array();
        if(streamDraft_.contains("transitions")&&!streamDraft_.at("transitions").is_array())valid=false;
        std::set<std::string> regionIds;
        for(const auto& row:regions){const auto id=StreamText(row,"id");if(id.empty()||!regionIds.insert(id).second)valid=false;
            if(row.is_object()&&row.contains("working_set")){
                if(!row.at("working_set").is_array())valid=false;
                else for(const auto& member:row.at("working_set"))if(!member.is_string())valid=false;
            }}
        for(const auto& gate:gates)if(StreamText(gate,"id").empty()||!regionIds.contains(StreamText(gate,"from"))||!regionIds.contains(StreamText(gate,"to")))valid=false;
        for(const auto& row:regions)for(const auto& member:StreamPreviewRows(row,"working_set"))if(!member.is_string()||!regionIds.contains(member.get<std::string>()))valid=false;
        result["draft_policy_valid"]=valid;
        std::string anchor=streamPreviewSelectedRegion_,destination;
        if(!streamPreviewSelectedGate_.empty())for(const auto& gate:gates)
            if(StreamText(gate,"id")==streamPreviewSelectedGate_){anchor=StreamText(gate,"from");destination=StreamText(gate,"to");break;}
        result["region"]=anchor;result["destination_region"]=destination;
        const auto stable=[&](const std::string& id){Json values=Json::array();for(const auto& row:regions)
            if(!id.empty()&&StreamText(row,"id")==id){
                if(!row.contains("working_set"))return Json::array({id});
                for(const auto& member:StreamPreviewRows(row,"working_set"))if(member.is_string())values.push_back(member);break;}return values;};
        result["stable_set"]=stable(anchor);result["destination_set"]=stable(destination);
        std::set<std::string> names;
        for(const auto& id:result.at("stable_set"))if(id.is_string())names.insert(id.get<std::string>());
        for(const auto& id:result.at("destination_set"))if(id.is_string())names.insert(id.get<std::string>());
        result["transition_union"]=names;
        for(const auto& gate:gates){const auto id=StreamText(gate,"id"),from=StreamText(gate,"from"),to=StreamText(gate,"to");
            if(anchor.empty()||from!=anchor||id.empty())continue;
            const auto destinationSet=stable(to);std::set<std::string> unionNames;
            for(const auto& member:result.at("stable_set"))if(member.is_string())unionNames.insert(member.get<std::string>());
            for(const auto& member:destinationSet)if(member.is_string())unionNames.insert(member.get<std::string>());
            result["outgoing"].push_back({{"transition",id},{"destination_region",to},{"destination_set",destinationSet},
                {"transition_union",unionNames},{"union_capacity",nullptr},{"witness_stale",stale}});
        }
        if(!valid){result["capacity_status"]="invalid-draft";return result;}
        if(stale)return result;
        Json witnesses=StreamPreviewRows(streamPreviewReport_,"working_sets");
        if(!streamPreview_)if(const auto* info=StreamingInfo()){
            if(!info->contains("mission_document")||info->at("mission_document")!=streamDraft_){result["capacity_status"]="pending-cook";return result;}
            witnesses=StreamPreviewRows(*info,"working_sets");}
        const auto same=[&](const Json& row,const Json& set){std::set<std::string> left,right;
            for(const auto& id:StreamPreviewRows(row,"source_regions"))if(id.is_string())left.insert(id.get<std::string>());
            for(const auto& id:set)if(id.is_string())right.insert(id.get<std::string>());return left==right;};
        for(const auto& row:witnesses)if(!anchor.empty()&&StreamText(row,"anchor")==anchor){
            if(same(row,result.at("stable_set")))result["stable_capacity"]=row;
            if(same(row,result.at("transition_union")))result["union_capacity"]=row;
            for(auto& outgoing:result["outgoing"])if(same(row,outgoing.at("transition_union")))outgoing["union_capacity"]=row;
        }
        if(!result.at("stable_capacity").is_null()&&!result.at("union_capacity").is_null())result["capacity_status"]="checked";
        return result;
    }
    Json StreamPreviewState() const {
        Json result={{"active",StreamPreviewActive()},{"mode",streamPreview_?"cooked-runtime-preview":"full-authoring"},
            {"automatic",streamPreviewAutomatic_},{"error",streamPreviewError_},{"selection",StreamPreviewSelection()},
            {"camera_outside_regions",streamPreview_&&!StreamPreviewCameraInside()},
            {"stale_draft",streamPreview_&&AuthorSnapshot()!=streamPreviewSnapshot_},
            {"source",streamPreviewSource_},{"source_saved_sha256",streamPreviewSourceSha_},{"partition_saved_sha256",streamPreviewPartitionSha_},
            {"rendered_models",streamPreviewRendered_},{"rendered_triangles",0},{"triangle_capacity",streamPreviewTriangleCapacity_},
            {"identity",Json::object()}};
        if(streamPreview_){
            const auto& package=streamPreview_->package;
            result["identity"]={{"play_fingerprint",package.at("fingerprint")},{"package_sha256",package.at("stream").at("package_sha256")},
                {"package_path",package.at("stream").at("package_path")},{"source_saved_sha256",streamPreviewSourceSha_},{"partition_saved_sha256",streamPreviewPartitionSha_},
                {"cooked_source_sha256",streamPreviewReport_.value("source_sha256",std::string())},
                {"cooked_partition_sha256",streamPreviewPartitionCookSha_},{"content_identity_sha256",streamPreviewReport_.value("content_identity_sha256",std::string())}};
            result["package_sha256"]=package.at("stream").at("package_sha256");
            result["play_fingerprint"]=package.at("fingerprint");result["game"]=streamPreview_->State();result["stream"]=result.at("game").at("stream");
            result["camera"]={{"position",DlPlaySession::J(streamPreview_->game.player)},{"yaw",streamPreview_->game.yaw},{"pitch",streamPreview_->game.pitch}};
            result["regions"]=streamPreviewReport_.value("regions",Json::array());
            result["cooked_partition"]=streamPreviewSnapshot_.at("partition");
            result["policy_regions"]=package.at("stream").at("policy_regions");result["transitions"]=package.at("stream").at("transitions");
            size_t triangles=0;for(const auto& row:streamPreviewRendered_)triangles+=row.value("triangles",size_t(0));result["rendered_triangles"]=triangles;
        }
        return result;
    }
    void RestoreStreamPreviewScene(Scene& scene) {
        if(streamPreviewScene_!=&scene)return;
        for(const auto& [id,renderer]:streamPreviewRenderers_)if(id<scene.meshRenderers.size())scene.meshRenderers[id]=renderer;
        if(streamPreviewEntity_&&*streamPreviewEntity_<scene.alive.size())scene.DeleteEntity(*streamPreviewEntity_);
        streamPreviewRenderers_.clear();streamPreviewEntity_.reset();streamPreviewScene_=nullptr;scene.renderablesDirty=true;
    }
    void StopStreamPreview() {
        if(streamPreviewScene_)RestoreStreamPreviewScene(*streamPreviewScene_);
        if(scene_&&scene_->cameraId&&streamPreviewAuthorCamera_)scene_->transforms[*scene_->cameraId]=*streamPreviewAuthorCamera_;
        streamPreviewAuthorCamera_.reset();streamPreview_.reset();streamPreviewRendered_=Json::array();streamPreviewAccumulator_=0;streamPreviewMeshValid_=false;streamPreviewCameraOutside_=false;
    }
    static glm::vec3 StreamPreviewVector(DlVec3 p){return {p.x,p.y,p.z};}
    void ReadStreamPreviewCamera(const Scene& scene) {
        if(!streamPreview_||!scene.cameraId)return;
        const auto& camera=scene.transforms[*scene.cameraId];const float eye=dl_player_eye(&streamPreview_->game).y-streamPreview_->game.player.y;
        streamPreview_->game.player={camera.position.x,camera.position.y-eye,camera.position.z};
        const glm::vec3 forward=camera.rotation*glm::vec3(0,0,-1);
        streamPreview_->game.yaw=std::atan2(forward.x,forward.z);streamPreview_->game.pitch=std::asin(std::clamp(forward.y,-1.f,1.f));
    }
    bool StreamPreviewCameraInside() const {
        if(!streamPreview_)return false;
        const auto* stream=streamPreview_->Stream();return dl_stream_scene_region_at(&stream->coordinator.scene,streamPreview_->game.player)!=0;
    }
    void ServiceStreamPreview() {
        const auto* stream=streamPreview_->Stream();const auto camera=streamPreview_->game.player;
        streamPreviewCameraOutside_=!StreamPreviewCameraInside();
        // The legacy policy has no outside-region fallback. A free inspection
        // camera may leave the authored envelope while the last valid interest
        // point keeps its published bank; re-entry resumes the shared policy.
        const bool hold=streamPreviewCameraOutside_&&stream->coordinator.scene.bootstrap->stream->policy==DL_STREAM_POLICY_LEGACY;
        if(hold)streamPreview_->game.player=streamPreviewDemandPosition_;else streamPreviewDemandPosition_=camera;
        try {streamPreview_->ServiceStream();}catch(...){streamPreview_->game.player=camera;throw;}
        streamPreview_->game.player=camera;
    }
    void SetStreamPreviewCamera(const Json& args) {
        if(!streamPreview_)throw std::runtime_error("Start a cooked runtime preview first");
        auto position=streamPreview_->game.player;float yaw=streamPreview_->game.yaw,pitch=streamPreview_->game.pitch;
        if(args.contains("position"))position=DlPlaySession::V(args.at("position"));
        const auto angle=[&](const char* key,float& value){if(args.contains(key)){value=args.at(key).get<float>();if(!std::isfinite(value))throw std::runtime_error("Camera angle must be finite");}};
        angle("yaw",yaw);angle("pitch",pitch);
        streamPreview_->game.player=position;streamPreview_->game.yaw=yaw;streamPreview_->game.pitch=std::clamp(pitch,-1.4f,1.4f);
        if(scene_&&scene_->cameraId){auto& camera=scene_->transforms[*scene_->cameraId];camera.position=StreamPreviewVector(dl_player_eye(&streamPreview_->game));
            camera.rotation=glm::angleAxis(yaw+glm::pi<float>(),glm::vec3(0,1,0))*glm::angleAxis(streamPreview_->game.pitch,glm::vec3(1,0,0));}
    }
    void SyncStreamPreviewMesh(Scene& scene) {
        if(!streamPreview_)return;
        if(streamPreviewScene_!=&scene)BindStreamPreview(scene);
        // Preserve authoring transforms and entity liveness. Split character
        // pieces are presentation entities and need the same renderer hiding.
        const auto hide=[&](EntityId id){if(id>=scene.meshRenderers.size())return;
            if(!streamPreviewRenderers_.contains(id))streamPreviewRenderers_[id]=scene.meshRenderers[id];
            if(scene.meshRenderers[id]){scene.meshRenderers[id].reset();scene.renderablesDirty=true;}};
        for(const auto& [id,name]:previewIds_)hide(id);
        for(const auto& [name,character]:characters_)for(const auto& [id,bone]:character.joints)hide(id);
        const auto& game=streamPreview_->game;const DlLevel& level=*game.level;
        const auto& previous=streamPreviewRenderedGame_;const uint32_t revision=streamPreview_->Stream()->coordinator.scene.revision;
        bool changed=!streamPreviewMeshValid_||revision!=streamPreviewRenderedRevision_||game.level!=previous.level||
            game.door_open!=previous.door_open||game.open_objects!=previous.open_objects||game.collected_objects!=previous.collected_objects||
            game.completed_objectives!=previous.completed_objectives||game.complete!=previous.complete||game.elapsed!=previous.elapsed||game.enemy_count!=previous.enemy_count;
        for(int i=0;!changed&&i<game.enemy_count;++i){const auto& a=game.enemies[i];const auto& b=previous.enemies[i];
            changed=a.position.x!=b.position.x||a.position.y!=b.position.y||a.position.z!=b.position.z||a.yaw!=b.yaw;}
        if(!changed)return;
        std::fill(streamPreviewVertices_.begin(),streamPreviewVertices_.end(),Vertex{});
        size_t cursor=0;streamPreviewRendered_=Json::array();
        const auto rotation=[](DlVec3 r){return glm::angleAxis(r.z,glm::vec3(0,0,1))*glm::angleAxis(r.y,glm::vec3(0,1,0))*glm::angleAxis(r.x,glm::vec3(1,0,0));};
        for(int mi=0;mi<level.model_count;++mi){const auto& model=level.models[mi];if(!dl_game_model_visible(&game,&model))continue;
            const auto& mesh=level.meshes[model.mesh];DlVec3 position=model.position,angles=model.rotation;
            if(model.role==DL_MODEL_GUARD&&model.enemy_index>=0&&model.enemy_index<game.enemy_count){const auto& enemy=game.enemies[model.enemy_index];const auto& def=level.enemies[model.enemy_index];
                position.x+=enemy.position.x-def.spawn.x;position.y+=enemy.position.y-def.spawn.y;position.z+=enemy.position.z-def.spawn.z;angles.y+=enemy.yaw-def.yaw;}
            if(model.role==DL_MODEL_OBJECTIVE)angles.y+=game.elapsed*.45f;
            const auto q=rotation(angles);const bool open=dl_game_model_open(&game,&model);
            glm::vec2 hinge{};if(open&&model.id&&streamPreview_->package.at("door_hinges").contains(model.id)){
                const auto& row=streamPreview_->package.at("door_hinges").at(model.id);hinge={row.at(0).get<float>(),row.at(1).get<float>()};}
            const auto point=[&](int index){glm::vec3 p=StreamPreviewVector(mesh.vertices[index])*StreamPreviewVector(model.scale);
                if(open){const float x=p.x-hinge.x,z=p.z-hinge.y;p.x=hinge.x+z;p.z=hinge.y-x;}return StreamPreviewVector(position)+q*p;};
            const size_t triangles=size_t(mesh.index_count/3);if(cursor+triangles*6>streamPreviewVertices_.size())throw std::runtime_error("Cooked viewport exceeds triangle capacity");
            for(int ti=0;ti<mesh.index_count/3;++ti){glm::vec3 points[3];for(int corner=0;corner<3;++corner)points[corner]=point(mesh.indices[ti*3+corner]);
                auto normal=glm::cross(points[1]-points[0],points[2]-points[0]);normal=glm::length(normal)>1e-7f?glm::normalize(normal):glm::vec3(0,1,0);
                for(int side=0;side<2;++side){if(side&&!model.double_sided){cursor+=3;continue;}for(int corner=0;corner<3;++corner){const int c=side?2-corner:corner,index=mesh.indices[ti*3+c];auto& vertex=streamPreviewVertices_[cursor++];
                    vertex.position=points[c];vertex.normal=normal*float(side?-1:1);
                    vertex.color={model.color[0]/255.f,model.color[1]/255.f,model.color[2]/255.f};
                    if(model.baked_colors[game.door_open?1:0]){const auto* rgba=model.baked_colors[game.door_open?1:0]+ti*24+side*12+c*4;vertex.color={rgba[0]/255.f,rgba[1]/255.f,rgba[2]/255.f};}
                    if(mesh.uvs)vertex.texcoord={mesh.uvs[index].u,mesh.uvs[index].v};
                    if(mesh.normals){const auto n=mesh.normals[index];glm::vec3 direction=q*glm::vec3(n.x/127.f,n.y/127.f,n.z/127.f);
                        if(glm::length(direction)>1e-7f)vertex.normal=glm::normalize(direction)*float(side?-1:1);}
                }}
            }
            streamPreviewRendered_.push_back({{"id",model.id?model.id:""},{"role",model.role},{"region",uint32_t(mesh.stream_key>>40)},
                {"triangles",triangles},{"position",DlPlaySession::J(position)},{"cooked",true}});
        }
        if(streamPreviewMesh_&&!engine_.GetAssetMgr().UpdateMeshVertices(*streamPreviewMesh_,streamPreviewVertices_))throw std::runtime_error("Cooked viewport mesh update failed");
        streamPreviewRenderedGame_=game;streamPreviewRenderedRevision_=revision;streamPreviewMeshValid_=true;
        scene.renderablesDirty=true;
    }
    void BindStreamPreview(Scene& scene) {
        if(!streamPreview_)return;
        // Call RestoreStreamPreviewScene before a reload; obsolete entity IDs
        // are never applied to the freshly loaded authoring scene.
        streamPreviewScene_=&scene;streamPreviewRenderers_.clear();streamPreviewMeshValid_=false;
        if(streamPreviewVertices_.empty())streamPreviewVertices_.resize(streamPreviewTriangleCapacity_*6);
        if(!streamPreviewMesh_){MeshData mesh;mesh.vertices=streamPreviewVertices_;mesh.indices.resize(mesh.vertices.size());
            for(size_t i=0;i<mesh.indices.size();++i)mesh.indices[i]=uint32_t(i);
            streamPreviewMesh_=engine_.GetAssetMgr().RegisterImportedMesh("gen://dl64-stream-preview/bounded-published",std::move(mesh));}
        const auto entity=scene.NewEntity("Cooked runtime preview (published geometry)");streamPreviewEntity_=entity.id;
        scene.isImported[entity.id]=true;scene.AddMeshRenderer(entity,MeshRenderer{*streamPreviewMesh_,engine_.GetAssetMgr().LoadMaterialFromUri("gen://basic"),MeshRendererState::Ready});
        SyncStreamPreviewMesh(scene);
    }
    void StartStreamPreview(const Json& args) {
        if(engine_.IsPlaying()||Busy()||!pendingAction_.empty())throw std::runtime_error("Finish Play or pending editor work before starting runtime preview");
        if(!streamDraft_.is_object())throw std::runtime_error("Runtime preview requires a mission partition");
        const bool automatic=args.value("automatic",false);const auto preset=args.value("start_preset",std::string("default"));
        SyncPreviewTransforms();const auto snapshot=AuthorSnapshot();
        const auto stage=queue_/"stream-preview-snapshot.json";AtomicWrite(stage,document_.dump(2)+"\n");
        std::vector<std::wstring> command{L"cook",L"--level",source_.wstring(),L"--staged",stage.wstring()};StagePartition(command,queue_/"stream-preview-partition.json");
        const auto cooked=ProjectCommand(command);
        if(!cooked.value("native_play_supported",false))throw std::runtime_error(cooked.at("streaming").at("status").value("error",std::string("Runtime preview needs a bounded, valid mission cook")));
        const fs::path prepared=cooked.at("output").get<std::string>();auto session=std::make_unique<DlPlaySession>(ReadJson(prepared/"generated/play.json"),preset);
        if(!session->Streamed())throw std::runtime_error("Cook did not produce a streaming mission");session->ConfigureStreamTransport(args);
        const auto report=ReadJson(prepared/"generated/mission_report.json");
        StopStreamPreview();streamPreview_=std::move(session);streamPreviewSnapshot_=snapshot;streamPreviewReport_=report;
        streamPreviewSource_=source_.string();streamPreviewSourceSha_=sourceSavedSha_;streamPreviewPartitionSha_=streamSavedSha_;
        streamPreviewPartitionCookSha_=cooked.at("streaming").at("mission_manifest_sha256");
        streamPreviewAutomatic_=automatic;streamPreviewError_.clear();streamPreviewAccumulator_=0;
        streamPreviewDemandPosition_=streamPreview_->game.player;streamPreviewCameraOutside_=false;
        if(scene_&&scene_->cameraId)streamPreviewAuthorCamera_=scene_->transforms[*scene_->cameraId];
        SetStreamPreviewCamera(Json::object());if(scene_)BindStreamPreview(*scene_);
        status_="Cooked runtime preview shows only published geometry. Full Authoring restores the source scene.";
    }
    void StepStreamPreview(unsigned ticks,const Json& input=Json()) {
        if(!streamPreview_)throw std::runtime_error("Start a cooked runtime preview first");
        if(ticks<1||ticks>600)throw std::runtime_error("Preview steps must be 1..600");
        const bool simulate=!input.is_null();DlInput controls{};if(simulate)controls=DlPlaySession::Input(input);
        if(scene_)ReadStreamPreviewCamera(*scene_);
        const auto* stream=streamPreview_->Stream();
        if(simulate&&stream->coordinator.scene.bootstrap->stream->policy==DL_STREAM_POLICY_LEGACY&&!StreamPreviewCameraInside())
            throw std::runtime_error("Simulation steps require the preview camera inside a mission region");
        for(unsigned i=0;i<ticks;++i){if(simulate){streamPreview_->Step(controls);controls.use=controls.noise=controls.jump=controls.restart=false;
                streamPreviewDemandPosition_=streamPreview_->game.player;}else ServiceStreamPreview();}
        if(simulate)SetStreamPreviewCamera(Json::object());
        if(scene_)SyncStreamPreviewMesh(*scene_);
    }
    void UpdateStreamPreview(Scene& scene,const FrameContext& frame) {
        if(!streamPreview_)return;
        try {
            ReadStreamPreviewCamera(scene);
            if(streamPreviewAutomatic_){
                streamPreviewAccumulator_+=std::clamp(double(frame.dt),0.,.1);unsigned ticks=0;
                while(streamPreviewAccumulator_>=DL_TICK_SECONDS&&ticks<6){ServiceStreamPreview();streamPreviewAccumulator_-=DL_TICK_SECONDS;++ticks;}
            }
            SyncStreamPreviewMesh(scene);
        }catch(const std::exception& error){streamPreviewError_=error.what();streamPreviewAutomatic_=false;}
    }
    Json StreamPreviewCommand(const std::string& op,const Json& args) {
        if(op=="start_stream_preview")StartStreamPreview(args);
        else if(op=="stop_stream_preview")StopStreamPreview();
        else if(op=="inspect_stream_preview"){}
        else if(op=="select_stream_preview"){
            const std::string region=args.value("region",std::string()),gate=args.value("transition",std::string());
            if(!region.empty()&&!gate.empty())throw std::runtime_error("Select either a region or a transition");
            bool valid=region.empty()&&gate.empty();if(streamDraft_.is_object()){
                for(const auto& row:StreamPreviewRows(streamDraft_,"regions"))if(!region.empty()&&StreamText(row,"id")==region)valid=true;
                for(const auto& row:StreamPreviewRows(streamDraft_,"transitions"))if(!gate.empty()&&StreamText(row,"id")==gate)valid=true;}
            if(!valid)throw std::runtime_error("Unknown preview region or transition");streamPreviewSelectedRegion_=region;streamPreviewSelectedGate_=gate;
        }else if(op=="step_stream_preview")StepStreamPreview(args.value("ticks",1u),args.value("input",Json()));
        else if(op=="set_stream_preview_camera")SetStreamPreviewCamera(args);
        else if(op=="set_stream_preview_transport"){
            if(!streamPreview_)throw std::runtime_error("Start a cooked runtime preview first");
            const bool automatic=args.value("automatic",streamPreviewAutomatic_);streamPreview_->ConfigureStreamTransport(args);
            streamPreviewAutomatic_=automatic;if(args.contains("automatic"))streamPreviewAccumulator_=0;
        }else throw std::runtime_error("Unknown runtime preview command");
        return StreamPreviewState();
    }
