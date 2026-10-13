    std::future<Json> preparation_;
    std::string preparationKind_,preparationRequest_,preparationSnapshot_,loadedPreviewFingerprint_;
    std::chrono::steady_clock::time_point preparationStarted_;
    Json lastPreparation_=Json::object();
    void ReloadPreviewKeepingCamera() {
        std::optional<Transform> transform;
        std::optional<Camera> camera;
        if(scene_&&scene_->cameraId) {
            transform=scene_->transforms[*scene_->cameraId];
            camera=scene_->cameras[*scene_->cameraId];
        }
        engine_.LoadScene(scenePath_);
        if(transform&&camera&&scene_&&scene_->cameraId) {
            scene_->transforms[*scene_->cameraId]=*transform;
            scene_->cameras[*scene_->cameraId]=*camera;
        }
    }
    void ApplyPreparedPreview(const Json& cooked) {
        InvalidatePreviewAssets(cooked);report_=cooked.at("report");
        const std::string fingerprint=cooked.value("preview_fingerprint",std::string());
        if(fingerprint.empty()||fingerprint!=loadedPreviewFingerprint_)ReloadPreviewKeepingCamera();
        loadedPreviewFingerprint_=fingerprint;
        structureDirty_=false;reloadPreview_=false;
    }
    void BeginPreparation(const std::string& kind,const std::string& file={},const std::string& request={}) {
        if(Busy())throw std::runtime_error("Finish the current preparation first");
        preparationKind_=kind;preparationRequest_=request;
        preparationSnapshot_=AuthorSnapshot().dump(2)+"\n";
        preparationStarted_=std::chrono::steady_clock::now();
        std::vector<std::wstring> args{L"cook",L"--level",kind=="open"?fs::path(file).wstring():source_.wstring()};
        if(kind=="preview") {
            const auto stage=queue_/"preview-snapshot.json";
            AtomicWrite(stage,document_.dump(2)+"\n");args.insert(args.end(),{L"--staged",stage.wstring()});
            StagePartition(args,queue_/"preview-partition.json");
        }
        const auto root=root_,queue=queue_;
        preparation_=std::async(std::launch::async,[root,queue,args]{return ProjectTool(root,queue,args);});
        status_=kind=="open"?"Opening "+file+"...":"Updating preview...";
    }
    void PollPreparation() {
        if(!preparation_.valid()||preparation_.wait_for(std::chrono::seconds(0))!=std::future_status::ready)return;
        const auto request=preparationRequest_;preparationRequest_.clear();
        const auto kind=preparationKind_;preparationKind_.clear();
        try {
            auto cooked=preparation_.get();
            if(AuthorSnapshot().dump(2)+"\n"!=preparationSnapshot_) {
                if(kind=="open")throw std::runtime_error("New edits detected; level switch cancelled to preserve them");
                return; // A newer draft will schedule its own preparation.
            }
            const auto sceneStarted=std::chrono::steady_clock::now();
            if(kind=="open") {
                InvalidatePreviewAssets(cooked);AdoptLevel(cooked);RegisterLevels();
                engine_.LoadScene(scenePath_);WriteSession();
                status_="Opened "+document_.at("title").get<std::string>()+".";
            } else {ApplyPreparedPreview(cooked);status_="Preview updated.";}
            const auto now=std::chrono::steady_clock::now();
            lastPreparation_={{"kind",kind},{"cache_hit",cooked.value("cache_hit",false)},
                {"cook_ms",cooked.value("prepare_ms",0.)},
                {"scene_ms",std::chrono::duration<double,std::milli>(now-sceneStarted).count()},
                {"total_ms",std::chrono::duration<double,std::milli>(now-preparationStarted_).count()}};
            Respond(request,true,{{"source",source_.string()},{"scene_path",scenePath_},{"preparation",lastPreparation_}});
        }catch(const std::exception& e){status_=e.what();ShowPanel("Build & Diagnostics");Respond(request,false,{{"error",e.what()}});}
    }
