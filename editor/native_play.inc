    std::unique_ptr<DlPlaySession> native_;
    std::unordered_map<std::string,CharacterInstance> editCharacters_;
    bool nativeRequested_=false, nativePresentation_=false;
    std::string nativeRequestId_, nativeStart_="default", nativeError_;
    bool debugCollision_=false,debugSight_=false;

    void DrawSimulationDebug(Scene& scene,DebugRenderer& renderer) {
        const auto line=[&](DlVec3 a,DlVec3 b,glm::vec4 color){renderer.DrawDebugLine({a.x,a.y,a.z},{b.x,b.y,b.z},color);};
        if(native_&&engine_.IsPlaying()) {
            const auto& g=native_->game;
            if(debugCollision_)for(int i=0;i<g.level->collider_count;++i) {
                const auto& b=g.level->colliders[i];DlVec3 corners[8];
                for(int k=0;k<8;++k){float x=(k&1?1.f:-1.f)*b.half_size.x,y=(k&2?1.f:-1.f)*b.half_size.y,z=(k&4?1.f:-1.f)*b.half_size.z;
                    corners[k]={b.center.x+std::cos(b.yaw)*x+std::sin(b.yaw)*z,b.center.y+y,b.center.z-std::sin(b.yaw)*x+std::cos(b.yaw)*z};}
                bool open=b.door&&(g.level->collider_objects?dl_game_object_open(&g,g.level->collider_objects[i]):g.door_open);
                for(int k=0;k<8;++k)for(int bit:{1,2,4})if(!(k&bit))line(corners[k],corners[k|bit],open?glm::vec4(.2f,.4f,.2f,1):glm::vec4(.2f,.9f,.4f,1));
            }
            if(debugCollision_)for(int i=0;i<g.level->floor_count;++i) {
                const auto& floor=g.level->floors[i];
                for(int j=0;j<3;++j)line(floor.vertices[j],floor.vertices[(j+1)%3],glm::vec4(.2f,.7f,1,1));
            }
            if(debugSight_)for(int i=0;i<g.enemy_count;++i) {
                const auto& e=g.enemies[i];DlVec3 eye{e.position.x,e.position.y+1.6f,e.position.z};
                line(eye,dl_player_eye(&g),e.sees_player?glm::vec4(1,.15f,.1f,1):glm::vec4(.8f,.7f,.1f,1));
                line(e.position,e.investigate_target,glm::vec4(.3f,.6f,1,1));
            }
        } else if(debugCollision_) {
            for(const auto& row:document_.at("entities"))if(row.contains("collider")) {
                const auto transform=PreviewTransform(row.at("transform"));const auto& box=row.at("collider");
                const glm::vec3 centre=Vector(box.at("center")),half=Vector(box.at("half_size"));glm::vec3 corners[8];
                for(int k=0;k<8;++k)corners[k]=transform.position+transform.rotation*((centre+half*glm::vec3(k&1?1:-1,k&2?1:-1,k&4?1:-1))*transform.scale);
                for(int k=0;k<8;++k)for(int bit:{1,2,4})if(!(k&bit))renderer.DrawDebugLine(corners[k],corners[k|bit],glm::vec4(.2f,.9f,.4f,1));
            }
            for(const auto& row:document_.at("entities"))if(row.contains("floor")) {
                const auto transform=PreviewTransform(row.at("transform"));const auto& points=row.at("floor").at("vertices");
                glm::vec3 vertices[3];
                for(int j=0;j<3;++j)vertices[j]=transform.position+transform.rotation*(Vector(points.at(j))*transform.scale);
                for(int j=0;j<3;++j)renderer.DrawDebugLine(vertices[j],vertices[(j+1)%3],glm::vec4(.2f,.7f,1,1));
            }
        }
        (void)scene;
    }

    Json NativeState() const {
        Json result=native_?native_->State():Json::object();
        result["mode"]=engine_.IsPlaying()?(engine_.IsPlayPaused()?"paused":"playing"):"edit";
        result["systems"]=engine_.PlaySystemNames();result["error"]=nativeError_;
        result["authoring_dirty"]=dirty_;
        const auto profile=document_.value("authoring_profile",std::string());
        result["native_play_supported"]=StreamingInfo()==nullptr&&profile!="external-scale-study"&&profile!="streamed-mission";
        if(const auto* streaming=StreamingInfo())result["streaming"]=*streaming;
        return result;
    }
    void StartNative(const std::string& preset="default",bool paused=false) {
        if(engine_.IsPlaying()||Busy()||!pendingAction_.empty())throw std::runtime_error("Stop play and finish pending editor work first");
        if(const auto* streaming=StreamingInfo())
            throw std::runtime_error(streaming->value("native_play_reason",std::string("Native Play for stream missions is unavailable; use Run level in Ares.")));
        if(document_.value("authoring_profile",std::string())=="streamed-mission")
            throw std::runtime_error("Streamed mission source needs a mission partition and bounded cook; Native Play is unavailable. Use Run level in Ares after preparing its mission manifest.");
        if(document_.contains("authoring_profile")&&document_.at("authoring_profile")=="external-scale-study")
            throw std::runtime_error("This source study is available for editor and Blender inspection. Open its bounded runtime level for Native Play; the full mission needs streaming and runtime capacity work.");
        SyncPreviewTransforms();
        const auto stage=queue_/"native-snapshot.json";
        AtomicWrite(stage,document_.dump(2)+"\n");
        const auto cooked=ProjectCommand({L"cook",L"--level",source_.wstring(),L"--staged",stage.wstring()});
        auto session=std::make_unique<DlPlaySession>(ReadJson(fs::path(cooked.at("output").get<std::string>())/"generated/play.json"),preset);
        ApplyPreparedPreview(cooked);
        native_=std::move(session);nativeError_.clear();
        engine_.EnterPlayMode();engine_.SetPlayPaused(paused);
        status_="Native Play uses the shared ROM simulation. Escape pauses; Stop returns to authoring.";
    }
    void EnterNative(Scene& scene) {
        if(!native_)throw std::runtime_error("Prepare a cooked play snapshot first");
        editCharacters_=characters_;
        for(auto& [id,instance]:characters_)if(instance.dynamicMesh) {
            MeshData mesh;mesh.vertices=instance.bindVertices;mesh.indices=instance.data->indices;
            instance.dynamicMesh=engine_.GetAssetMgr().RegisterImportedMesh(
                "gen://dl64-play/"+source_.stem().string()+"/"+id+"/"+instance.data->topologyHash,std::move(mesh));
            scene.meshRenderers[instance.parent]->mesh=*instance.dynamicMesh;
            scene.meshRenderers[instance.parent]->state=MeshRendererState::Ready;
        }
        nativePresentation_=true;SyncNative(scene);
    }
    void ExitNative(Scene&) {
        if(nativePresentation_||!editCharacters_.empty())characters_=std::move(editCharacters_);
        nativePresentation_=false;
        if(native_){native_->injected={};native_->inputOverride=false;dl_tick_clear_pending(&native_->clock);}
        if(scene_)UpdateCharacters(*scene_,0);
    }
    void SyncNative(Scene& scene) {
        if(!native_)return;
        const auto& g=native_->game;
        for(const auto& [id,name]:previewIds_) {
            if(id>=scene.transforms.size())continue;
            const auto* entity=Entity(name);if(!entity)continue;
            auto& transform=scene.transforms[id];
            if(entity->at("kind")=="guard") {
                for(int i=0;i<g.enemy_count;++i)if(name==g.level->enemies[i].id) {
                    const auto& e=g.enemies[i];transform.position={e.position.x,e.position.y,e.position.z};
                    const float authored=glm::radians(entity->at("transform").at("rotation")[1].get<float>());
                    transform.rotation=glm::angleAxis(e.yaw-authored,glm::vec3(0,1,0))*PreviewTransform(entity->at("transform")).rotation;
                }
            } else if(entity->at("kind")=="door") {
                transform=PreviewTransform(entity->at("transform"));
                DlModelInstance model{};model.id=name.c_str();model.role=DL_MODEL_DOOR;
                if(dl_game_model_open(&g,&model)) {
                    // Same scaled mesh-bounds hinge used by the ROM renderer.
                    const auto& hinge=native_->package.at("door_hinges").at(name);
                    const float x=hinge[0],z=hinge[1];
                    transform.position+=transform.rotation*glm::vec3(x-z,0,z+x);
                    transform.rotation=transform.rotation*glm::angleAxis(glm::half_pi<float>(),glm::vec3(0,1,0));
                }
            } else if(entity->at("kind")=="objective"||entity->at("kind")=="loot") {
                DlModelInstance model{};model.id=name.c_str();model.role=entity->at("kind")=="loot"?DL_MODEL_LOOT:DL_MODEL_OBJECTIVE;
                scene.alive[id]=dl_game_model_visible(&g,&model);
            }
        }
        if(scene.cameraId) {
            const auto eye=dl_player_eye(&g);auto& camera=scene.transforms[*scene.cameraId];
            camera.position={eye.x,eye.y,eye.z};
            camera.rotation=glm::angleAxis(g.yaw+glm::pi<float>(),glm::vec3(0,1,0))*glm::angleAxis(g.pitch,glm::vec3(1,0,0));
            scene.cameras[*scene.cameraId]->nearPlane=.05f;
        }
        UpdateCharacters(scene,0);scene.renderablesDirty=true;
    }
    void UpdateNative(Scene& scene,const FrameContext& frame) {
        if(!native_)return;
        try {
            DlInput input=native_->injected;
            if(!native_->inputOverride&&frame.input) {
                auto& keys=*frame.input;
                input.forward=float(keys.IsKeyDown(GLFW_KEY_W))-float(keys.IsKeyDown(GLFW_KEY_S));
                input.strafe=float(keys.IsKeyDown(GLFW_KEY_D))-float(keys.IsKeyDown(GLFW_KEY_A));
                input.turn=float(keys.IsKeyDown(GLFW_KEY_RIGHT))-float(keys.IsKeyDown(GLFW_KEY_LEFT));
                input.look=float(keys.IsKeyDown(GLFW_KEY_UP))-float(keys.IsKeyDown(GLFW_KEY_DOWN));
                input.crouch=keys.IsKeyDown(GLFW_KEY_Z);input.jump=keys.WasKeyPressed(GLFW_KEY_SPACE);
                input.use=keys.WasKeyPressed(GLFW_KEY_E);input.noise=keys.WasKeyPressed(GLFW_KEY_N);input.restart=keys.WasKeyPressed(GLFW_KEY_R);
                double dx=0,dy=0;keys.GetMouseDelta(dx,dy);
                if(frame.dt>0) {input.turn=std::clamp(input.turn+float(dx)*.002f/(2.3f*frame.dt),-1.f,1.f);
                    input.look=std::clamp(input.look-float(dy)*.002f/(1.7f*frame.dt),-1.f,1.f);}
            }
            if(frame.singleStep)native_->Step(input);else native_->Advance(frame.dt,input);
            native_->injected.use=native_->injected.noise=native_->injected.jump=native_->injected.restart=false;
            SyncNative(scene);
        } catch(const std::exception& error){nativeError_=error.what();engine_.SetPlayPaused(true);ShowPanel("Play Inspector");}
    }
    Json NativeCommand(const std::string& op,const Json& args) {
        if(op=="set_play_debug"){debugCollision_=args.value("collision",debugCollision_);debugSight_=args.value("sight",debugSight_);return {{"collision",debugCollision_},{"sight",debugSight_}};}
        if(op=="inspect_scene") {
            Json rows=Json::array();const auto& scene=engine_.GetActiveScene();
            for(auto id:scene.AliveEntities()){const auto& t=scene.transforms[id];rows.push_back({{"entity_id",id},{"name",scene.names[id]},
                {"source_id",previewIds_.contains(id)?previewIds_.at(id):""},{"position",{t.position.x,t.position.y,t.position.z}},
                {"rotation_quaternion",{t.rotation.x,t.rotation.y,t.rotation.z,t.rotation.w}},{"scale",{t.scale.x,t.scale.y,t.scale.z}},
                {"rendered",scene.meshRenderers[id].has_value()}});}
            return {{"mode",engine_.IsPlaying()?"play":"edit"},{"entities",rows},{"source",source_.string()},{"selected",selected_}};
        }
        if(op=="start_play"){StartNative(args.value("start_preset",std::string("default")),args.value("paused",true));return NativeState();}
        if(op=="stop_play"){engine_.ExitPlayMode();return NativeState();}
        if(op=="inspect_play")return NativeState();
        if(!native_)throw std::runtime_error("No native session");
        if(args.contains("expected_tick")&&args.at("expected_tick").get<uint64_t>()!=native_->clock.tick)
            throw std::runtime_error("Stale play-state expectation; inspect again");
        if(op=="query_play") {
            if(args.contains("from")&&args.contains("to"))return {{"line_of_sight",dl_line_of_sight(&native_->game,DlPlaySession::V(args.at("from")),DlPlaySession::V(args.at("to")))}};
            const auto position=DlPlaySession::V(args.at("position"));
            return {{"standing_clear",dl_position_clear(&native_->game,position,.18f,1.65f)},
                {"crouching_clear",dl_position_clear(&native_->game,position,.18f,.95f)},{"visibility",dl_visibility(&native_->game,position)}};
        }
        if(op=="get_play_trace") {
            const uint64_t since=args.value("since_tick",uint64_t(0));Json rows=Json::array();
            for(const auto& row:native_->trace)if(row.at("tick").get<uint64_t>()>=since)rows.push_back(row);
            return {{"events",rows},{"dropped",native_->traceDropped},{"tick",native_->clock.tick}};
        }
        if(op=="export_replay") {
            const auto path=queue_/"replays"/"latest.json";
            AtomicWrite(path,Json{{"format","darklantern-replay"},{"version",1},{"tick_rate",DL_TICK_RATE},
                {"fingerprint",native_->package.at("fingerprint")},{"start_preset",native_->preset<0?"default":native_->level.test_starts[native_->preset].id},
                {"inputs",native_->inputs},{"final_state",native_->State()}}.dump(2));
            return {{"path",path.string()},{"ticks",native_->inputs.size()}};
        }
        if(!engine_.IsPlaying())throw std::runtime_error("Start native Play first");
        if(op=="pause_play")engine_.SetPlayPaused(args.value("paused",true));
        else if(op=="restart_play"){native_->Restart();SyncNative(engine_.GetActiveScene());}
        else if(op=="set_play_input"){native_->injected=DlPlaySession::Input(args.value("input",Json::object()));native_->inputOverride=args.value("override",true);}
        else if(op=="step_play") {
            if(!engine_.IsPlayPaused())throw std::runtime_error("Pause before stepping");
            const int ticks=args.value("ticks",1);if(ticks<1||ticks>600)throw std::runtime_error("Step ticks must be 1..600");
            if(native_->inputs.size()+ticks>DlPlaySession::maxRecords)throw std::runtime_error("Recording capacity reached; export and restart");
            if(args.contains("input")){native_->injected=DlPlaySession::Input(args.at("input"));native_->inputOverride=true;}
            for(int i=0;i<ticks;++i)engine_.StepPlayMode();
        } else if(op=="replay_play") {
            if(!engine_.IsPlayPaused())throw std::runtime_error("Pause before replaying");
            const auto replay=ReadJson(queue_/"replays/latest.json");
            if(replay.at("format")!="darklantern-replay"||replay.at("version")!=1||replay.at("tick_rate")!=DL_TICK_RATE||
                replay.at("fingerprint")!=native_->package.at("fingerprint")||
                replay.at("start_preset")!=(native_->preset<0?"default":native_->level.test_starts[native_->preset].id))throw std::runtime_error("Replay/content or test start mismatch");
            const auto& rows=replay.at("inputs");if(!rows.is_array()||rows.size()>DlPlaySession::maxRecords)throw std::runtime_error("Invalid replay length");
            std::vector<DlInput> commands;for(const auto& row:rows)commands.push_back(DlPlaySession::Input(row));
            native_->Restart();for(auto input:commands)native_->Step(input);SyncNative(engine_.GetActiveScene());
        } else throw std::runtime_error("Unknown Play command");
        return NativeState();
    }
    void DrawNative() {
        if(BeginPanel("Play Inspector")) {
            if(native_) {
                const auto state=NativeState();
                ImGui::Text("Tick %llu | 60 Hz | dropped %.3fs",(unsigned long long)native_->clock.tick,native_->clock.dropped_seconds);
                ImGui::Text("Exposure %.3f | %s",native_->game.visibility,native_->game.caught?"Caught":native_->game.complete?"Complete":"Active");
                for(const auto& e:state.at("enemies")) {
                    const auto label=e.at("id").get<std::string>()+": "+e.at("state").get<std::string>();
                    if(ImGui::TreeNode(label.c_str())){ImGui::TextWrapped("%s",e.dump(2).c_str());ImGui::TreePop();}
                }
                if(ImGui::CollapsingHeader("Recent behavior events"))for(auto it=native_->trace.rbegin();it!=native_->trace.rend()&&it-native_->trace.rbegin()<30;++it)
                    ImGui::TextWrapped("%s",it->dump().c_str());
            }
            ImGui::Checkbox("Collision proxies",&debugCollision_);ImGui::SameLine();ImGui::Checkbox("Guard sight",&debugSight_);
            if(!nativeError_.empty())ImGui::TextWrapped("%s",nativeError_.c_str());
            if(!native_)ImGui::TextDisabled("Start Play to inspect the simulation.");
        }
        ImGui::End();
    }
