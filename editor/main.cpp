#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <future>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unordered_map>
#include <lightengine.h>
#include <imgui.h>
#include <imgui_internal.h>
#include <nlohmann/json.hpp>

namespace fs = std::filesystem;
using Json = nlohmann::json;

namespace
{
Json ReadJson(const fs::path& path)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("Cannot read " + path.string());
    Json result;
    input >> result;
    return result;
}

std::string ReadText(const fs::path& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot read " + path.string());
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

struct SessionLock
{
    HANDLE handle = nullptr;
    ~SessionLock() { if (handle) CloseHandle(handle); }
    void Acquire(const fs::path& queue)
    {
        const auto name = L"Local\\DarkLantern64Editor-" + std::to_wstring(std::hash<std::wstring>{}(queue.wstring()));
        handle = CreateMutexW(nullptr, FALSE, name.c_str());
        if (!handle) throw std::runtime_error("Cannot acquire editor session lock");
        if (GetLastError() == ERROR_ALREADY_EXISTS) throw std::runtime_error("This project editor is already open. Return to its window.");
    }
};

void AtomicWrite(const fs::path& path, const std::string& text)
{
    fs::create_directories(path.parent_path());
    fs::path temporary = path;
    temporary += ".tmp";
    {
        std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
        if (!output || !(output << text) || !output.flush())
            throw std::runtime_error("Cannot write " + temporary.string());
    }
    if (!MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("Cannot replace " + path.string());
}

std::wstring Quote(const std::wstring& value)
{
    // Windows argv quoting. No shell expansion and no executable user commands.
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t c : value)
    {
        if (c == L'\\') { ++slashes; continue; }
        if (c == L'\"') { out.append(slashes * 2 + 1, L'\\'); out += c; }
        else { out.append(slashes, L'\\'); out += c; }
        slashes = 0;
    }
    out.append(slashes * 2, L'\\');
    return out + L"\"";
}

int Run(const fs::path& cwd, const std::vector<std::wstring>& arguments, const fs::path& log, DWORD timeout = INFINITE)
{
    fs::create_directories(log.parent_path());
    std::wstring command;
    for (const auto& arg : arguments) { if (!command.empty()) command += L' '; command += Quote(arg); }
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    HANDLE output = CreateFileW(log.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                &security, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (output == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot open process log");
    HANDLE input = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                               &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    startup.hStdOutput = output;
    startup.hStdError = output;
    startup.hStdInput = input;
    PROCESS_INFORMATION process{};
    BOOL started = CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW,
                                  nullptr, cwd.c_str(), &startup, &process);
    CloseHandle(output);
    if (input != INVALID_HANDLE_VALUE) CloseHandle(input);
    if (!started) throw std::runtime_error("Cannot launch build tool (Windows error " + std::to_string(GetLastError()) + ")");
    const DWORD waited = WaitForSingleObject(process.hProcess, timeout);
    if (waited != WAIT_OBJECT_0)
    {
        TerminateProcess(process.hProcess, 1);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        throw std::runtime_error("Tool exceeded its execution deadline");
    }
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return static_cast<int>(code);
}

std::string Tail(const fs::path& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) return {};
    input.seekg(0, std::ios::end);
    const auto length = input.tellg();
    input.seekg(std::max<std::streamoff>(0, static_cast<std::streamoff>(length) - 14000));
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

Json ProjectTool(const fs::path& root, const fs::path& queue, const std::vector<std::wstring>& arguments)
{
    std::vector<std::wstring> command{L"python",(root/"tools/project_levels.py").wstring(),L"--root",root.wstring()};
    command.insert(command.end(),arguments.begin(),arguments.end());
    const auto log=queue/"project-tool.json";
    if (Run(root,command,log,120000)) throw std::runtime_error(Tail(log));
    return ReadJson(log);
}

bool focusProjectTabs = false;

void ProjectLayout(ImGuiID root)
{
    focusProjectTabs = true;
    ImGuiID main = root, right, bottom, audio, inspector, objects, levels;
    ImGui::DockBuilderSplitNode(main, ImGuiDir_Left, .20f, &levels, &main);
    ImGui::DockBuilderSplitNode(main, ImGuiDir_Right, .29f, &right, &main);
    ImGui::DockBuilderSplitNode(main, ImGuiDir_Down, .33f, &bottom, &main);
    ImGui::DockBuilderSplitNode(bottom, ImGuiDir_Right, .60f, &audio, &bottom);
    ImGui::DockBuilderSplitNode(right, ImGuiDir_Down, .59f, &inspector, &objects);
    ImGui::DockBuilderDockWindow("Viewport", main);
    ImGui::DockBuilderDockWindow("Levels", levels);
    ImGui::DockBuilderDockWindow("Audio Memory", audio);
    ImGui::DockBuilderDockWindow("Room Objects", objects);
    ImGui::DockBuilderDockWindow("Entity List", objects);
    ImGui::DockBuilderDockWindow("Object Properties", inspector);
    ImGui::DockBuilderDockWindow("Selected Entity", inspector);
    for (const char* name : {"Build & Diagnostics", "Imports", "Effects", "Background", "Image Viewer", "Texture Browser", "Material Library"})
        ImGui::DockBuilderDockWindow(name, bottom);
}

struct JobResult { bool ok = false; std::string message; Json report; Json project; };

class DemoEditor
{
  public:
    explicit DemoEditor(fs::path root, fs::path source, bool isolated = false) : root_(fs::weakly_canonical(root)), queue_(root_ / ".dev/editor/project")
    {
        if (isolated)
        {
            source = fs::weakly_canonical(source);
            const auto stem=source.stem().string();
            if (source.parent_path() != root_/"content" || source.extension() != ".json" || stem.empty() ||
                stem.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=std::string::npos)
                throw std::runtime_error("Choose a JSON level directly inside this project's content directory");
            queue_ = source.filename() == "first_room.json" ? root_/".dev/editor" : root_/".dev/editor/scenes"/source.stem();
        }
        sessionLock_.Acquire(queue_);
        fs::create_directories(queue_ / "requests");
        fs::create_directories(queue_ / "responses");
        catalog_ = ProjectCommand({L"list"});
        if (catalog_.at("levels").empty()) throw std::runtime_error("The project needs at least one valid level in content/");
        std::string filename = source.filename().string();
        if (!isolated && fs::exists(queue_/"project-state.json"))
            try { filename = ReadJson(queue_/"project-state.json").value("last_level",filename); } catch (...) {}
        if (!LevelEntry(filename)) filename = catalog_.at("levels")[0].at("file");
        source_ = root_/"content"/filename;
        LoadAudio();
    }

    void Initialize()
    {
        const Json cooked = ProjectCommand({L"cook",L"--level",source_.wstring()});
        AdoptLevel(cooked);
        engine_.SetEditorLayout(queue_ / "layout-project-v1.ini", ProjectLayout);
        engine_.SetSceneOpenHandler([this](const std::string& path) {
            try
            {
                for (const auto& level : catalog_.at("levels"))
                    if (path == "levels/" + level.at("stem").get<std::string>() + ".json")
                    { QueueAction({{"op","open"},{"file",level.at("file")}}); return; }
                throw std::runtime_error("Refresh the Levels list; that level is no longer registered");
            }
            catch (const std::exception& error) { status_=error.what(); }
        });
        engine_.SetSceneSaveHandler([this] { saveRequested_=true; });
        engine_.SetCloseRequestHandler([this] {
            try
            {
                SyncPreviewTransforms();
                if (Busy() || !captureId_.empty()) { status_="Wait for the current operation before closing."; return false; }
                if (!pendingAction_.empty()) return false;
                if (HasUnsaved()) { QueueAction({{"op","close"}}); return false; }
                return true;
            }
            catch (const std::exception& error) { status_=error.what(); return false; }
        });
        engine_.Init(root_/"build/project/editor-assets", "DarkLantern64 | Project Editor");
        engine_.SetWindowSize(1440, 960);
        initialized_=true;
        RegisterLevels();
        engine_.LoadScene(scenePath_);
        WriteSession();
    }

    void Loop()
    {
        while (engine_.IsRunning())
        {
            SyncPreviewTransforms();
            PollJob();
            PollCommands();
            if (saveRequested_)
            {
                saveRequested_=false;
                if (!Busy() && pendingAction_.empty()) Save(false);
            }
            ProcessAction();
            if (reloadPreview_) { reloadPreview_ = false; engine_.LoadScene(scenePath_); }
            engine_.Update();
            engine_.Render();
            ++frame_;
            if (frame_ % 120 == 0)
                AtomicWrite(queue_ / "state.json", Json{{"pid",GetCurrentProcessId()},{"frame",frame_},{"dirty",dirty_},
                            {"busy",Busy()},{"selected",selected_},{"status",status_},{"layout",Layout()}}.dump(2));
            if (!captureId_.empty() && frame_ >= captureFrame_)
            {
                const auto id = captureId_;
                captureId_.clear();
                const fs::path output = queue_ / "captures" / (id + ".png");
                fs::create_directories(output.parent_path());
                const bool ok = engine_.CaptureFramePng(output);
                Respond(id, ok, ok ? Json{{"path",output.string()},{"type","LightEngine viewport"}} : Json{{"error","CaptureFramePng failed"}});
            }
        }
        if (job_.valid()) { job_.wait(); PollJob(); }
    }

  private:
    fs::path root_, queue_, source_, output_;
    SessionLock sessionLock_;
    LightEngine engine_;
    Json document_, report_, audioConfig_, audioDraft_, audioReport_;
    Json prefabs_ = Json::array();
    char assetPackUri_[256] = "assets/loot/pack.json";
    std::string selectedPrefab_;
    bool highlightNewProp_ = false;
    Scene* scene_ = nullptr;
    std::unordered_map<EntityId, std::string> previewIds_;
    std::unordered_map<EntityId, Transform> previewTransforms_;
    std::string scenePath_;
    Json catalog_, pendingAction_ = Json::object();
    std::string browserLevel_, sourceSnapshot_, testStart_ = "default", actionRequestId_;
    bool initialized_=false, saveRequested_=false, actionReady_=false, continueAfterSave_=false;
    bool showUnsaved_=false, showNew_=false, newDuplicate_=false, showDelete_=false;
    char newName_[65]="new_room", newTitle_[81]="New room";
    bool newInBundle_=true;
    std::string selected_, status_ = "Edit XYZ transforms or gameplay properties, then Save + Cook.";
    std::string audioStatus_, audioScenario_;
    std::string jobId_, jobSnapshot_, captureId_;
    std::future<JobResult> job_;
    bool dirty_ = false, reloadPreview_ = false, structureDirty_ = false;
    int frame_ = 0, captureFrame_ = 0;

    bool Busy() const { return job_.valid(); }
    static std::string LevelLabel(std::string title)
    {
        const std::string prefix="DarkLantern64 - ";
        if (title.starts_with(prefix)) title.erase(0,prefix.size());
        return title;
    }
    bool HasUnsaved() const { return dirty_ || audioDraft_ != audioConfig_; }
    Json ProjectCommand(const std::vector<std::wstring>& arguments) { return ProjectTool(root_,queue_,arguments); }
    const Json* LevelEntry(const std::string& file) const
    {
        if (!catalog_.is_object() || !catalog_.contains("levels")) return nullptr;
        for (const auto& level : catalog_.at("levels")) if (level.at("file")==file) return &level;
        return nullptr;
    }
    void RegisterLevels()
    {
        if (!initialized_) return;
        std::vector<std::pair<std::string,std::string>> levels;
        for (const auto& level : catalog_.at("levels"))
        {
            const auto path="levels/"+level.at("stem").get<std::string>()+".json";
            levels.emplace_back(path,LevelLabel(level.at("title"))+" ("+level.at("file").get<std::string>()+")");
            engine_.OnLevelLoaded(path,[this](Scene& scene) { BindScene(scene); });
            engine_.OnImGui(path,[this] { Draw(); });
        }
        engine_.SetRegisteredLevels(std::move(levels));
    }
    void RefreshLevels()
    {
        catalog_=ProjectCommand({L"list"});
        if (!LevelEntry(browserLevel_)) browserLevel_=source_.filename().string();
        RegisterLevels();
    }
    void WriteSession()
    {
        AtomicWrite(queue_/"project-state.json",Json{{"last_level",source_.filename().string()}}.dump(2));
        AtomicWrite(queue_/"session.json",Json{{"pid",GetCurrentProcessId()},{"root",root_.string()},
            {"source",source_.string()},{"output",output_.string()},{"scene_path",scenePath_},{"project_editor",true},
            {"operations",{"inspect","list_levels","open_level","create_level","duplicate_level","delete_level","update_level","play_level","play_menu",
                "import_asset_pack","add_prop","set_entity","add_enemy","duplicate_enemy","add_waypoint","delete_entity","set_environment","set_material",
                "set_preview_transform","save","build","capture","get_layout","reset_layout","reload","get_audio_memory","set_audio_config","recalculate_audio","quit"}}}.dump(2));
    }
    void InvalidatePreviewAssets(const Json& cooked)
    {
        for (const auto& item : cooked.value("changed_preview_assets",Json::array()))
        {
            const fs::path path=item.get<std::string>();
            if (path.extension()==".gltf") engine_.GetAssetMgr().InvalidateMeshByPath(path);
            else engine_.GetAssetMgr().InvalidateTextureByPath(path);
        }
    }
    void AdoptLevel(const Json& cooked)
    {
        const fs::path source=cooked.at("source").get<std::string>();
        const auto snapshot=ReadText(source);
        const Json document=Json::parse(snapshot);
        source_=source; sourceSnapshot_=snapshot; document_=document;
        output_=fs::path(cooked.at("output").get<std::string>());
        scenePath_=cooked.at("scene_path"); report_=cooked.at("report"); catalog_=cooked.at("catalog");
        selected_=document_.at("entities").empty()?"":document_.at("entities")[0].at("id").get<std::string>();
        browserLevel_=source_.filename().string(); testStart_="default";
        prefabs_=Json::array(); selectedPrefab_.clear();
        previewIds_.clear(); previewTransforms_.clear();
        dirty_=false; structureDirty_=false; reloadPreview_=false;
    }
    void OpenLevel(const std::string& file)
    {
        RefreshLevels();
        const auto* level=LevelEntry(file);
        if (!level) throw std::runtime_error("Level is no longer available; refresh the Levels list");
        const Json cooked=ProjectCommand({L"cook",L"--level",fs::path(file).wstring(),L"--expected-sha256",fs::path(level->at("source_sha256").get<std::string>()).wstring()});
        InvalidatePreviewAssets(cooked);
        AdoptLevel(cooked);
        RegisterLevels();
        engine_.LoadScene(scenePath_);
        WriteSession();
        status_="Opened "+document_.at("title").get<std::string>()+".";
    }
    void QueueAction(Json action, const std::string& requestId={})
    {
        if (Busy() || !pendingAction_.empty() || !captureId_.empty()) throw std::runtime_error("Finish the current editor operation first");
        SyncPreviewTransforms();
        const auto op=action.at("op").get<std::string>();
        const bool leaving=op!="update" && (op!="delete" || action.at("file")==source_.filename().string());
        if (leaving && HasUnsaved() && !requestId.empty()) throw std::runtime_error("Unsaved changes; save/apply or discard before changing levels");
        if ((op=="delete" || op=="update" || op=="duplicate") && action.contains("file"))
        {
            const auto* level=LevelEntry(action.at("file"));
            if (!level) throw std::runtime_error("Level is no longer available");
            action["expected_sha256"]=level->at("source_sha256");
        }
        pendingAction_=std::move(action); actionRequestId_=requestId;
        actionReady_=!leaving || !HasUnsaved(); showUnsaved_=!actionReady_;
    }
    void ProcessAction()
    {
        if (!actionReady_ || pendingAction_.empty() || Busy()) return;
        const std::string pendingOp=pendingAction_.at("op");
        const bool leaving=pendingOp!="update" && (pendingOp!="delete" || pendingAction_.at("file")==source_.filename().string());
        if (leaving && HasUnsaved() && !pendingAction_.value("discard",false))
        {
            actionReady_=false;
            if (actionRequestId_.empty()) showUnsaved_=true;
            else
            {
                Respond(actionRequestId_,false,{{"error","New unsaved edits detected; save before changing levels"}});
                actionRequestId_.clear(); pendingAction_=Json::object();
            }
            return;
        }
        const Json action=pendingAction_;
        const auto request=actionRequestId_;
        pendingAction_=Json::object(); actionRequestId_.clear(); actionReady_=false;
        try
        {
            const std::string op=action.at("op");
            Json result;
            if (action.value("discard",false)) { OpenLevel(source_.filename().string()); audioDraft_=audioConfig_; }
            if (op=="close") { engine_.RequestExit(); result={{"closing",true}}; }
            else if (op=="open") { OpenLevel(action.at("file")); result={{"source",source_.string()},{"scene_path",scenePath_}}; }
            else
            {
                RefreshLevels();
                std::vector<std::wstring> arguments{fs::path(op).wstring()};
                if (op!="create")
                {
                    if (action.at("file")==source_.filename().string() && ReadText(source_)!=sourceSnapshot_)
                        throw std::runtime_error("Active level changed on disk; reload or reconcile it before changing its project settings");
                    const auto* level=LevelEntry(action.at("file"));
                    if (!level) throw std::runtime_error("Level no longer exists");
                    arguments.insert(arguments.end(),{L"--level",fs::path(level->at("file").get<std::string>()).wstring(),
                        L"--expected-sha256",fs::path(action.at("expected_sha256").get<std::string>()).wstring()});
                }
                for (const char* key : {"name","title"}) if (action.contains(key))
                    arguments.insert(arguments.end(),{std::wstring(L"--")+fs::path(key).wstring(),fs::path(action.at(key).get<std::string>()).wstring()});
                if (action.contains("in_bundle")) arguments.insert(arguments.end(),{L"--in-bundle",action.at("in_bundle").get<bool>()?L"true":L"false"});
                if (op=="delete" && action.at("file")==source_.filename().string())
                {
                    const auto replacement=std::find_if(catalog_.at("levels").begin(),catalog_.at("levels").end(),[&](const Json& level) { return level.at("file")!=action.at("file"); });
                    if (replacement==catalog_.at("levels").end()) throw std::runtime_error("Keep at least one project level; create another before deleting this one");
                    OpenLevel(replacement->at("file"));
                }
                result=ProjectCommand(arguments);
                catalog_=result.at("catalog"); RegisterLevels();
                if (op=="create" || op=="duplicate") OpenLevel(result.at("level").at("file"));
                else if (op=="update")
                {
                    if (action.at("file")==source_.filename().string())
                    {
                        sourceSnapshot_=ReadText(source_);
                        if (action.contains("title")) document_["title"]=action.at("title");
                    }
                    status_="Updated level settings.";
                }
                else { browserLevel_=source_.filename().string(); status_="Level removed. Recoverable copy: "+result.value("backup",std::string(".dev/editor/deleted-levels")); }
                WriteSession();
            }
            Respond(request,true,result);
        }
        catch (const std::exception& error) { status_=error.what(); Respond(request,false,{{"error",error.what()}}); }
    }
    Json* Entity(const std::string& id)
    {
        for (auto& entity : document_["entities"]) if (entity["id"] == id) return &entity;
        return nullptr;
    }
    Json Layout() const
    {
        Json windows = Json::array();
        for (const char* name : {"Levels","Audio Memory","Viewport","Room Objects","Object Properties","Build & Diagnostics",
                                "Entity List","Selected Entity","Imports","Effects","Background","Image Viewer","Texture Browser","Material Library"})
            if (auto* w = ImGui::FindWindowByName(name))
                windows.push_back({{"name",name},{"dock_id",w->DockId},{"position",{w->Pos.x,w->Pos.y}},
                                   {"size",{w->Size.x,w->Size.y}},{"active",bool(w->Active)},{"tab_id",w->TabId},{"tab_visible",bool(w->DockTabIsVisible)}});
        return {{"settings",(queue_/"layout-project-v1.ini").string()},{"windows",windows}};
    }
    void Respond(const std::string& id, bool ok, Json result)
    {
        if (id.empty()) return;
        AtomicWrite(queue_ / "responses" / (id + ".json"), Json{{"id",id},{"ok",ok},{"result",std::move(result)}}.dump(2));
    }
    void Save(bool build, const std::string& requestId = {}, const std::string& play = {})
    {
        if (Busy()) throw std::runtime_error("Build already running");
        jobId_ = requestId;
        jobSnapshot_ = document_.dump(2) + "\n";
        status_ = build ? "Validating content, updating preview and building ROM..." : "Validating and cooking content...";
        const auto root = root_, queue = queue_, source = source_;
        const auto snapshot = jobSnapshot_;
        const auto savedSnapshot=sourceSnapshot_, preset=testStart_;
        job_ = std::async(std::launch::async, [root, queue, source, snapshot, savedSnapshot, preset, build, play]() -> JobResult
        {
            try
            {
                const auto stage = queue / "staging" / source.filename();
                if (ReadText(source)!=savedSnapshot) throw std::runtime_error("Level changed on disk. Your edits are retained; reload or reconcile the saved file before saving.");
                AtomicWrite(stage, snapshot);
                Json cooked=ProjectTool(root,queue,{L"cook",L"--level",source.wstring(),L"--staged",stage.wstring()});
                if (ReadText(source)!=savedSnapshot) throw std::runtime_error("Level changed during cooking; your edits are retained and the source was not overwritten.");
                AtomicWrite(source, snapshot);
                Json report=cooked.at("report");
                if (build || !play.empty())
                {
                    std::vector<std::wstring> arguments{L"python",(root/"tools/build.py").wstring()};
                    if (play=="menu") arguments.insert(arguments.end(),{L"--bundle",(root/"content/level_bundle.json").wstring()});
                    else arguments.insert(arguments.end(),{L"--level",source.wstring()});
                    if (play=="level" && preset!="default") arguments.insert(arguments.end(),{L"--start-preset",fs::path(preset).wstring()});
                    if (!play.empty()) arguments.push_back(L"--run");
                    const int code=Run(root,arguments,queue/"rom-build.log");
                    if (code) return {false,"Content saved and cooked; game build/launch failed.\n"+Tail(queue/"rom-build.log"),report,cooked};
                }
                return {true,!play.empty()?"Saved content and launched the game in Ares.":build?"Saved, cooked, and built ROM.":"Saved content and refreshed the preview.",report,cooked};
            }
            catch (const std::exception& error) { return {false,error.what(),{}}; }
        });
    }
    void PollJob()
    {
        if (!job_.valid() || job_.wait_for(std::chrono::seconds(0)) != std::future_status::ready) return;
        auto result = job_.get();
        status_ = result.message;
        if (!result.report.empty())
        {
            report_ = result.report;
            dirty_ = document_.dump(2) + "\n" != jobSnapshot_;
            if (!dirty_) structureDirty_ = false;
            sourceSnapshot_=jobSnapshot_;
            InvalidatePreviewAssets(result.project);
            try { RefreshLevels(); }
            catch (const std::exception& error)
            {
                result.ok=false; result.message+="\nSaved, but the project catalog could not refresh: "+std::string(error.what()); status_=result.message;
            }
            reloadPreview_ = true;
        }
        if (continueAfterSave_)
        {
            continueAfterSave_=false;
            if (result.ok && !HasUnsaved())
            {
                if (pendingAction_.value("file",std::string())==source_.filename().string())
                    if (const auto* level=LevelEntry(source_.filename().string())) pendingAction_["expected_sha256"]=level->at("source_sha256");
                actionReady_=true;
            }
            else { pendingAction_=Json::object(); actionRequestId_.clear(); }
        }
        Respond(jobId_, result.ok, {{"message",result.message},{"report",result.report}});
        jobId_.clear();
    }
    static glm::vec3 Vector(const Json& value)
    {
        return {value[0].get<float>(), value[1].get<float>(), value[2].get<float>()};
    }
    static Transform PreviewTransform(const Json& value)
    {
        Transform result;
        result.position = Vector(value.at("position"));
        result.rotation = glm::quat(glm::radians(Vector(value.at("rotation"))));
        result.scale = Vector(value.at("scale"));
        return result;
    }
    static void CheckVector(const Json& value, const std::string& label, float minimum, float maximum)
    {
        if (!value.is_array() || value.size() != 3) throw std::runtime_error(label + " must contain XYZ");
        for (const auto& component : value)
            if (!component.is_number() || !std::isfinite(component.get<float>()) ||
                component.get<float>() < minimum || component.get<float>() > maximum)
                throw std::runtime_error(label + " contains an invalid or out-of-range value");
    }
    static void PatchTransform(Json& transform, const Json& patch)
    {
        if (!patch.is_object()) throw std::runtime_error("transform must be an object");
        for (const auto& [key, value] : patch.items())
        {
            if (key == "position") CheckVector(value, key, -1024, 1024);
            else if (key == "rotation") CheckVector(value, key, -3600, 3600);
            else if (key == "scale") CheckVector(value, key, .001f, 1024);
            else throw std::runtime_error("Unknown transform field: " + key);
            transform[key] = value;
        }
    }
    void BindScene(Scene& scene)
    {
        scene_ = &scene;
        previewIds_.clear(); previewTransforms_.clear();
        for (EntityId id : scene.AliveEntities())
            if (auto* entity = Entity(scene.names[id]))
            {
                previewIds_[id] = scene.names[id];
                scene.transforms[id] = PreviewTransform(entity->at("transform"));
                previewTransforms_[id] = scene.transforms[id];
            }
        scene.renderablesDirty = true;
    }
    void PushTransform(const std::string& id)
    {
        if (!scene_) return;
        for (const auto& [previewId, sourceId] : previewIds_)
            if (sourceId == id && scene_->alive[previewId])
            {
                scene_->transforms[previewId] = PreviewTransform(Entity(id)->at("transform"));
                previewTransforms_[previewId] = scene_->transforms[previewId];
                scene_->renderablesDirty = true;
            }
    }
    void SyncPreviewTransforms()
    {
        if (!scene_) return;
        for (const auto& [id, sourceId] : previewIds_)
        {
            if (id >= scene_->alive.size() || !scene_->alive[id]) continue;
            // Deleted canonical objects can remain in the old preview until the
            // next cook. Their stale gizmos must never recreate source content.
            if (!Entity(sourceId)) continue;
            const auto& current = scene_->transforms[id];
            const auto& old = previewTransforms_.at(id);
            const bool moved = glm::length(current.position-old.position) > .00001f;
            const bool scaled = glm::length(current.scale-old.scale) > .00001f;
            const bool rotated = std::abs(glm::dot(current.rotation,old.rotation)) < .9999999f;
            if (!moved && !scaled && !rotated) continue;
            Json patch = Json::object();
            if (moved) patch["position"] = {current.position.x,current.position.y,current.position.z};
            if (scaled) patch["scale"] = {current.scale.x,current.scale.y,current.scale.z};
            if (rotated)
            {
                auto degrees = glm::degrees(glm::eulerAngles(current.rotation));
                patch["rotation"] = {degrees.x,degrees.y,degrees.z};
            }
            try
            {
                auto* entity = Entity(sourceId);
                Json transform = entity->at("transform");
                PatchTransform(transform,patch);
                (*entity)["transform"] = transform;
                selected_ = sourceId;
                dirty_ = true;
                previewTransforms_[id] = current;
            }
            catch (const std::exception& error) { status_ = error.what(); }
        }
    }
    const Json& EnemyTypes() const
    {
        static const Json empty = Json::array();
        return report_.contains("enemy_types") ? report_.at("enemy_types") : empty;
    }
    const Json& EnemyType(const std::string& id) const
    {
        for (const auto& type : EnemyTypes()) if (type.at("id") == id) return type;
        throw std::runtime_error("Unknown enemy type: " + id + ". Rebuild the editor content catalog if code changed.");
    }
    static bool ValidId(const std::string& id)
    {
        return !id.empty() && id.size() <= 64 &&
            ((id[0] >= 'A' && id[0] <= 'Z') || (id[0] >= 'a' && id[0] <= 'z')) &&
            id.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") == std::string::npos;
    }
    std::set<std::string> UsedIds() const
    {
        std::set<std::string> used;
        for (const char* group : {"assets","materials","entities"})
            for (const auto& item : document_.at(group)) used.insert(item.at("id").get<std::string>());
        return used;
    }
    static std::string NewId(const Json& args, const char* key, const std::string& prefix, std::set<std::string>& used)
    {
        std::string result;
        if (args.contains(key))
        {
            if (!args.at(key).is_string()) throw std::runtime_error(std::string(key) + " must be a stable ID string");
            result = args.at(key).get<std::string>();
            if (!ValidId(result) || used.contains(result)) throw std::runtime_error("Invalid or already used object ID: " + result);
        }
        else
        {
            for (int suffix = 1; ; ++suffix)
            {
                result = prefix.substr(0, 52) + "-" + std::to_string(suffix);
                if (!used.contains(result)) break;
            }
        }
        used.insert(result);
        return result;
    }
    void CheckCapacity(size_t extraEntities, int extraEnemies) const
    {
        if (document_.at("entities").size() + extraEntities > 256) throw std::runtime_error("The level limit is 256 objects");
        int count = 0;
        for (const auto& entity : document_.at("entities")) if (entity.at("kind") == "guard") ++count;
        if (count + extraEnemies > 16) throw std::runtime_error("The current authoring limit is 16 enemies; this is not a frame-rate guarantee");
    }
    Json PlacementPosition(const Json& args)
    {
        if (args.contains("position")) { CheckVector(args.at("position"),"position",-1024,1024); return args.at("position"); }
        if (const auto* selected = Entity(selected_); selected &&
            (selected->at("kind") == "guard" || selected->at("kind") == "waypoint" || selected->at("kind") == "spawn"))
            return selected->at("transform").at("position");
        for (const char* kind : {"guard","spawn"})
            for (const auto& entity : document_.at("entities"))
                if (entity.at("kind") == kind) return entity.at("transform").at("position");
        return Json::array({0,0,0});
    }
    void StructureChanged(const std::string& id)
    {
        selected_ = id;
        dirty_ = structureDirty_ = true;
        status_ = "Object list updated. Set XYZ placement, then Save + Cook to refresh viewport models and markers.";
    }
    Json ImportAssetPack(const Json& args)
    {
        if (!args.is_object() || args.size() != 1 || !args.contains("uri") || !args.at("uri").is_string())
            throw std::runtime_error("import_asset_pack requires only a content-relative uri");
        const std::string uri = args.at("uri").get<std::string>();
        if (uri.empty() || uri.size() > 240) throw std::runtime_error("Pack URI must contain 1-240 characters");
        const auto stage = queue_/"staging/asset-pack-level.json";
        const auto catalog = queue_/"staging/asset-pack-catalog.json";
        const auto output = queue_/"staging/asset-pack-import.json";
        AtomicWrite(stage,document_.dump(2)+"\n");
        AtomicWrite(catalog,prefabs_.dump(2)+"\n");
        const int code = Run(root_,{L"python",(root_/"tools/asset_pack.py").wstring(),
            L"--level",stage.wstring(),L"--pack",fs::path(uri).wstring(),
            L"--asset-root",(root_/"content").wstring(),L"--catalog",catalog.wstring(),
            L"--output",output.wstring()},queue_/"asset-pack-import.log",10000);
        if (code) throw std::runtime_error(Tail(queue_/"asset-pack-import.log"));
        Json imported = ReadJson(output);
        Json document = imported.at("document"), prefabs = imported.at("prefabs");
        const bool changed = document != document_;
        document_ = std::move(document);
        prefabs_ = std::move(prefabs);
        if (selectedPrefab_.empty() && !prefabs_.empty()) selectedPrefab_ = prefabs_[0].at("id");
        dirty_ = dirty_ || changed;
        status_ = "Asset pack imported. Choose a prefab, place it, and edit XYZ. Save + Cook validates the models and textures.";
        return {{"uri",uri},{"prefabs",prefabs_},{"dirty",dirty_},{"definitions_changed",changed},{"validation","geometry and textures checked on Save + Cook"}};
    }
    Json AddProp(const Json& args)
    {
        if (!args.is_object()) throw std::runtime_error("add_prop arguments must be an object");
        for (const auto& [key,value] : args.items())
            if (key != "prefab" && key != "id" && key != "position" && key != "rotation" && key != "scale" && key != "loot_highlight")
                throw std::runtime_error("Unsupported prop field: " + key);
        if (args.contains("loot_highlight") && !args.at("loot_highlight").is_boolean())
            throw std::runtime_error("loot_highlight must be a boolean");
        if (!args.contains("prefab") || !args.at("prefab").is_string()) throw std::runtime_error("Choose an imported prefab ID");
        const std::string prefabId = args.at("prefab");
        const auto prefab = std::find_if(prefabs_.begin(),prefabs_.end(),[&](const Json& item) { return item.at("id") == prefabId; });
        if (prefab == prefabs_.end()) throw std::runtime_error("Unknown prefab: " + prefabId + "; import its pack first");
        CheckCapacity(1,0);
        size_t models = 0;
        for (const auto& entity : document_.at("entities")) if (entity.contains("model")) ++models;
        if (models >= 128) throw std::runtime_error("The level limit is 128 model instances");
        auto used = UsedIds();
        const std::string id = NewId(args,"id",prefabId,used);
        Json transform = {{"position",PlacementPosition(args)},{"rotation",{0,0,0}},{"scale",prefab->at("scale")}};
        Json patch = Json::object();
        for (const char* field : {"position","rotation","scale"})
            if (args.contains(field)) patch[field] = args.at(field);
        PatchTransform(transform,patch);
        Json entity = {{"id",id},{"kind","static"},{"model",prefab->at("model")},{"material",prefab->at("material")},
            {"transform",transform},{"loot_highlight",args.value("loot_highlight",false)}};
        document_["entities"].push_back(entity);
        StructureChanged(id);
        return {{"entity",entity},{"prefab",prefabId},{"dirty",true},{"preview_refresh_pending",true}};
    }
    static bool DeletableProp(const Json& entity)
    {
        return entity.at("kind") == "static" && entity.contains("model") && !entity.contains("collider");
    }
    Json AddEnemy(const Json& args)
    {
        CheckCapacity(1,1);
        Json source;
        if (args.contains("template_id"))
        {
            const auto* entity = Entity(args.at("template_id").get<std::string>());
            if (!entity || entity->at("kind") != "guard") throw std::runtime_error("template_id must identify an existing enemy");
            source = *entity;
        }
        else
            for (const auto& entity : document_.at("entities"))
                if (entity.at("kind") == "guard") { source = entity; break; }
        if (source.is_null())
        {
            bool mesh = false, material = false;
            for (const auto& asset : document_.at("assets")) if (asset.at("id") == "mesh-guard") mesh = true;
            for (const auto& item : document_.at("materials")) if (item.at("id") == "mat-guard") material = true;
            if (!mesh || !material) throw std::runtime_error("Add mesh-guard and mat-guard assets to this level, or supply an existing enemy template");
            source = {{"kind","guard"},{"model","mesh-guard"},{"material","mat-guard"},
                {"transform",{{"position",{0,0,0}},{"rotation",{0,0,0}},{"scale",{1,1,1}}}}};
        }
        auto used = UsedIds();
        const std::string id = NewId(args,"id","enemy",used);
        source["id"] = id;
        source["enemy_type"] = args.value("enemy_type",std::string("watchman"));
        EnemyType(source.at("enemy_type").get<std::string>());
        source["behavior"] = args.value("behavior",std::string("sentry"));
        if (source.at("behavior") != "sentry") throw std::runtime_error("New enemies start as sentries; author at least two route points before switching to patrol");
        source["patrol"] = Json::array();
        for (const char* key : {"speed","sight_range","hearing_range"}) source.erase(key);
        source["transform"]["position"] = PlacementPosition(args);
        document_["entities"].push_back(source);
        StructureChanged(id);
        return {{"entity",source},{"dirty",true},{"preview_refresh_pending",true}};
    }
    Json DuplicateEnemy(const Json& args)
    {
        const auto* original = Entity(args.at("id").get<std::string>());
        if (!original || original->at("kind") != "guard") throw std::runtime_error("id must identify an existing enemy");
        Json copy = *original;
        auto used = UsedIds();
        const std::string newId = NewId(args,"new_id",copy.at("id").get<std::string>() + "-copy",used);
        const Json oldPosition = copy.at("transform").at("position");
        const Json newPosition = args.contains("position") ? args.at("position") : oldPosition;
        CheckVector(newPosition,"position",-1024,1024);
        copy["id"] = newId;
        copy["transform"]["position"] = newPosition;
        Json waypoints = Json::array(), route = Json::array();
        std::unordered_map<std::string,std::string> replacements;
        for (const auto& ref : copy.value("patrol",Json::array()))
        {
            const std::string oldId = ref.get<std::string>();
            if (!replacements.contains(oldId))
            {
                const auto* originalPoint = Entity(oldId);
                if (!originalPoint || originalPoint->at("kind") != "waypoint") throw std::runtime_error("Cannot duplicate an enemy with a broken patrol reference: " + oldId);
                Json point = *originalPoint;
                const std::string pointId = NewId(Json::object(),"id",newId + "-point",used);
                point["id"] = pointId;
                for (size_t axis = 0; axis < 3; ++axis)
                    point["transform"]["position"][axis] = point.at("transform").at("position").at(axis).get<float>() + newPosition[axis].get<float>() - oldPosition[axis].get<float>();
                CheckVector(point.at("transform").at("position"),"Copied waypoint position",-1024,1024);
                waypoints.push_back(point);
                replacements[oldId] = pointId;
            }
            route.push_back(replacements.at(oldId));
        }
        copy["patrol"] = route;
        CheckCapacity(1 + waypoints.size(),1);
        document_["entities"].push_back(copy);
        for (const auto& point : waypoints) document_["entities"].push_back(point);
        StructureChanged(newId);
        return {{"entity",copy},{"created_waypoints",waypoints},{"dirty",true},{"preview_refresh_pending",true}};
    }
    Json AddWaypoint(const Json& args)
    {
        CheckCapacity(1,0);
        auto used = UsedIds();
        const std::string id = NewId(args,"id","waypoint",used);
        Json point = {{"id",id},{"kind","waypoint"},
            {"transform",{{"position",PlacementPosition(args)},{"rotation",{0,0,0}},{"scale",{1,1,1}}}}};
        document_["entities"].push_back(point);
        StructureChanged(id);
        return {{"entity",point},{"dirty",true},{"preview_refresh_pending",true}};
    }
    Json DeleteEntity(const Json& args)
    {
        const std::string id = args.at("id");
        const auto* entity = Entity(id);
        if (!entity || (entity->at("kind") != "guard" && entity->at("kind") != "waypoint" && !DeletableProp(*entity)))
            throw std::runtime_error("Only enemies, unused waypoints and non-colliding static props can be deleted in Room Workshop");
        for (const auto& candidate : document_.at("entities"))
            for (const auto& ref : candidate.value("patrol",Json::array()))
                if (ref == id) throw std::runtime_error("Waypoint is used by " + candidate.at("id").get<std::string>() + "; remove it from that route first");
        auto& entities = document_["entities"];
        entities.erase(std::remove_if(entities.begin(),entities.end(),[&](const Json& item) { return item.at("id") == id; }),entities.end());
        StructureChanged(entities.empty() ? "" : entities[0].at("id").get<std::string>());
        return {{"deleted",id},{"dirty",true},{"preview_refresh_pending",true}};
    }
    void CheckEnemy(const Json& entity)
    {
        if (entity.at("kind") != "guard") return;
        EnemyType(entity.value("enemy_type",std::string("watchman")));
        const std::string behavior = entity.value("behavior",std::string("patrol"));
        if (behavior != "patrol" && behavior != "sentry") throw std::runtime_error("behavior must be patrol or sentry");
        const Json route = entity.value("patrol",Json::array());
        if (!route.is_array() || route.size() > 32 || (behavior == "patrol" && route.size() < 2))
            throw std::runtime_error("Patrol behavior requires 2-32 points. Choose Sentry while building a new route.");
        for (const auto& ref : route)
        {
            if (!ref.is_string()) throw std::runtime_error("Route entries must be waypoint IDs");
            const auto* point = Entity(ref.get<std::string>());
            if (!point || point->at("kind") != "waypoint") throw std::runtime_error("Unknown waypoint: " + ref.get<std::string>());
        }
        for (const char* key : {"speed","sight_range","hearing_range"})
            if (entity.contains(key))
            {
                const auto& value = entity.at(key);
                const bool speed = std::string(key) == "speed";
                if (!value.is_number() || !std::isfinite(value.get<float>()) || value.get<float>() < (speed ? .05f : .1f) || value.get<float>() > (speed ? 4.f : 128.f))
                    throw std::runtime_error(std::string(key) + (speed ? " must be 0.05-4" : " must be 0.1-128"));
            }
    }
    void SetEntity(const Json& args)
    {
        const std::string id = args.at("id");
        Json* entity = Entity(id);
        if (!entity) throw std::runtime_error("Unknown object ID: " + id);
        const auto& patch = args.at("patch");
        if (!patch.is_object()) throw std::runtime_error("patch must be an object");
        static const std::set<std::string> allowed{"transform","speed","sight_range","hearing_range","enemy_type","behavior","radius","intensity","target","patrol","color","loot_highlight"};
        Json candidate = *entity;
        for (const auto& [key,value] : patch.items())
        {
            if (key == "loot_highlight")
            {
                if (candidate.at("kind") != "static" || !candidate.contains("model") || !value.is_boolean())
                    throw std::runtime_error("loot_highlight requires a boolean on a static model prop");
                candidate[key] = value;
                continue;
            }
            const bool lightColor = key == "color" && candidate.at("kind") == "light";
            const bool enemyProperty = candidate.at("kind") == "guard" &&
                (key == "enemy_type" || key == "behavior" || key == "patrol" || key == "speed" || key == "sight_range" || key == "hearing_range");
            if (!allowed.contains(key) || (!candidate.contains(key) && !lightColor && !enemyProperty)) throw std::runtime_error("Unsupported property for object: " + key);
            if (key == "transform") { PatchTransform(candidate[key],value); continue; }
            if (enemyProperty && value.is_null() && (key == "speed" || key == "sight_range" || key == "hearing_range"))
            { candidate.erase(key); continue; }
            if (lightColor) CheckVector(value,"Light RGB",0,1);
            else if (key == "enemy_type" || key == "behavior") { if (!value.is_string()) throw std::runtime_error(key + " must be a string"); }
            else if (key == "target") { if (!value.is_string()) throw std::runtime_error("target must be an ID"); }
            else if (key == "patrol")
            {
                if (!value.is_array() || value.size() > 32 ||
                    !std::all_of(value.begin(), value.end(), [](const Json& ref) { return ref.is_string(); }))
                    throw std::runtime_error("patrol must contain at most 32 waypoint IDs");
            }
            else if (!value.is_number() || !std::isfinite(value.get<float>())) throw std::runtime_error("Tuning value must be finite");
            if (key == "intensity" && (value.get<float>() < 0 || value.get<float>() > 16))
                throw std::runtime_error("Light intensity must be between 0 and 16");
            candidate[key] = value;
        }
        CheckEnemy(candidate);
        *entity = candidate;
        selected_ = id;
        dirty_ = true;
        PushTransform(id);
    }
    static Json DefaultEnvironment()
    {
        return {{"ambient",{.012,.018,.03}},{"moon_direction",{-.35,.8,.4}},
            {"moon_color",{.42,.60,1.0}},{"moon_intensity",.55},
            {"fog_color",{.025,.045,.08}},{"fog_near",18.0},{"fog_far",45.0},
            {"sky_top",{.008,.015,.04}},{"sky_bottom",{.04,.065,.11}},{"exposure",1.0}};
    }
    void SetEnvironment(const Json& patch)
    {
        if (!patch.is_object()) throw std::runtime_error("Environment patch must be an object");
        Json candidate = document_.value("environment",DefaultEnvironment());
        for (const auto& [key,value] : patch.items())
        {
            if (key == "moon_direction")
            {
                CheckVector(value,key,-1,1);
                if (glm::dot(Vector(value),Vector(value)) <= .0001f) throw std::runtime_error("Moon direction must have a nonzero length greater than 0.01");
            }
            else if (key == "ambient" || key == "moon_color" || key == "fog_color" || key == "sky_top" || key == "sky_bottom")
                CheckVector(value,key,0,1);
            else if (key == "moon_intensity" || key == "exposure" || key == "fog_near" || key == "fog_far")
            {
                const float minimum = key == "exposure" ? .05f : 0;
                const float maximum = key == "moon_intensity" ? 16 : key == "exposure" ? 8 : 1024;
                if (!value.is_number() || !std::isfinite(value.get<float>()) || value.get<float>() < minimum || value.get<float>() > maximum)
                    throw std::runtime_error("Invalid environment value: " + key);
            }
            else throw std::runtime_error("Unknown environment field: " + key);
            candidate[key] = value;
        }
        if (candidate.at("fog_far").get<float>() <= candidate.at("fog_near").get<float>())
            throw std::runtime_error("Fog far distance must exceed fog near distance");
        document_["environment"] = candidate;
        dirty_ = true;
    }
    Json* Material(const std::string& id)
    {
        for (auto& material : document_["materials"]) if (material["id"] == id) return &material;
        return nullptr;
    }
    void SetMaterial(const Json& args)
    {
        const std::string id = args.at("id");
        Json* material = Material(id);
        if (!material) throw std::runtime_error("Unknown material: " + id);
        const auto& patch = args.at("patch");
        if (!patch.is_object()) throw std::runtime_error("Material patch must be an object");
        Json candidate = *material;
        for (const auto& [key,value] : patch.items())
        {
            if (key == "emissive") CheckVector(value,"Emission RGB",0,1);
            else if (key == "color")
            {
                if (!value.is_array() || value.size() != 4) throw std::runtime_error("Material color must contain RGBA");
                for (const auto& channel : value)
                    if (!channel.is_number() || !std::isfinite(channel.get<float>()) || channel.get<float>() < 0 || channel.get<float>() > 1)
                        throw std::runtime_error("Material RGBA channels must be in 0..1");
            }
            else if (key == "texture")
            {
                if (value.is_null()) { candidate.erase(key); continue; }
                if (!value.is_object() || !value.contains("uri") || !value.at("uri").is_string() || value.value("format","") != "RGBA16")
                    throw std::runtime_error("Texture requires a content-relative URI, width, height and RGBA16 format");
                for (const char* size : {"width","height"})
                    if (!value.contains(size) || !value.at(size).is_number_integer() || value.at(size).get<int>() < 1 ||
                        value.at(size).get<int>() > 64 || (value.at(size).get<int>() & (value.at(size).get<int>() - 1)))
                        throw std::runtime_error("Texture dimensions must be powers of two from 1 to 64");
                if (value.at("width").get<int>() * value.at("height").get<int>() * 2 > 4096)
                    throw std::runtime_error("RGBA16 texture exceeds the 4 KiB texture memory budget");
            }
            else throw std::runtime_error("Unsupported material field: " + key);
            candidate[key] = value;
        }
        *material = candidate;
        dirty_ = true;
    }
    void SetPreviewTransform(const Json& args)
    {
        const std::string sourceId = args.at("id");
        auto* entity = Entity(sourceId);
        if (!entity || !scene_) throw std::runtime_error("Unknown preview object");
        Json transform = entity->at("transform");
        PatchTransform(transform,args.at("transform"));
        for (const auto& [id, name] : previewIds_)
            if (name == sourceId && scene_->alive[id])
            {
                scene_->transforms[id] = PreviewTransform(transform);
                scene_->renderablesDirty = true;
                // Exercise exactly the same round trip as a stock viewport gizmo.
                SyncPreviewTransforms();
                return;
            }
        throw std::runtime_error("Preview object is unavailable; recook the scene");
    }
    void LoadAudio()
    {
        const auto manifest = root_/"content/audio_budget.json";
        const auto report = root_/"build/generated/audio_memory_report.json";
        if (fs::exists(manifest)) audioConfig_ = audioDraft_ = ReadJson(manifest);
        if (fs::exists(report)) audioReport_ = ReadJson(report);
        if (!audioReport_.empty() && audioReport_.contains("scenarios") && !audioReport_["scenarios"].empty())
        {
            const auto& scenarios = audioReport_["scenarios"];
            if (!std::any_of(scenarios.begin(),scenarios.end(),[this](const Json& item) { return item.at("id") == audioScenario_; }))
                audioScenario_ = scenarios[0].at("id");
        }
    }
    Json AudioState() const
    {
        return {{"status","planning_only"},{"config",audioConfig_},{"report",audioReport_},
                {"selected_scenario_id",audioScenario_},{"draft_dirty",audioConfig_!=audioDraft_},{"message",audioStatus_}};
    }
    static void PatchAudio(Json& config, const Json& patch)
    {
        if (!patch.is_object()) throw std::runtime_error("Audio patch must be an object");
        const std::unordered_map<std::string,std::set<std::string>> allowed{
            {"output",{"sample_rate_hz","buffer_frames","buffer_count"}},
            {"budget",{"ram_bytes"}},
            {"buffer_pool",{"decoded_bytes","decoder_workspace_bytes","channel_capacity"}},
            {"allowances",{"runtime_bytes","allocator_bytes","unmeasured_bytes","reserve_bytes"}}};
        for (const auto& [group,fields] : patch.items())
        {
            if (group == "budget" && fields.is_null()) { config[group] = nullptr; continue; }
            if (!allowed.contains(group) || !fields.is_object()) throw std::runtime_error("Unsupported audio group: "+group);
            if (group == "budget" && config[group].is_null())
                config[group] = {{"status","illustrative"},{"note","Editable planning envelope; not an approved project allocation."}};
            for (const auto& [name,value] : fields.items())
            {
                if (!allowed.at(group).contains(name) || !value.is_number_integer() || value.get<int64_t>() < 0 || value.get<int64_t>() > 8388608)
                    throw std::runtime_error("Audio values must be bounded nonnegative integers; unsupported field: "+name);
                config[group][name] = value;
            }
        }
    }
    void RecalculateAudio(const Json& candidate, bool save)
    {
        if (!save && audioDraft_ != audioConfig_) throw std::runtime_error("Unapplied audio changes; Apply or Discard draft before recalculating the saved plan");
        const auto stage = queue_/"staging/audio_budget.json";
        const auto output = queue_/"staging/audio_memory_report.json";
        AtomicWrite(stage,candidate.dump(2)+"\n");
        const int result = Run(root_,{L"python",(root_/"tools/estimate_audio.py").wstring(),L"--manifest",stage.wstring(),L"--output",output.wstring()},queue_/"audio-estimate.log",10000);
        if (result) throw std::runtime_error("Audio estimate failed; saved plan preserved.\n"+Tail(queue_/"audio-estimate.log"));
        const auto report = ReadJson(output);
        if (report.value("schema_version",0)!=1 || report.value("status","")!="planning_only")
            throw std::runtime_error("Unsupported audio report schema");
        if (save)
        {
            if (ReadJson(root_/"content/audio_budget.json") != audioConfig_)
                throw std::runtime_error("Audio manifest changed outside the editor; Discard draft, then Recalculate saved plan before applying changes");
            AtomicWrite(root_/"content/audio_budget.json",candidate.dump(2)+"\n");
        }
        AtomicWrite(root_/"build/generated/audio_memory_report.json",report.dump(2)+"\n");
        audioConfig_ = audioDraft_ = candidate;
        audioReport_ = report;
        audioStatus_ = save ? "Planning assumptions saved and recalculated. No ROM or runtime allocation changed." : "Recalculated saved planning assumptions.";
    }
    void PollCommands()
    {
        int processed = 0;
        for (const auto& entry : fs::directory_iterator(queue_/"requests"))
        {
            if (++processed > 8) break;
            if (!entry.is_regular_file() || entry.path().extension() != ".json") continue;
            const std::string id = entry.path().stem().string();
            if (id.size() != 32 || id.find_first_not_of("0123456789abcdef") != std::string::npos) continue;
            if (fs::exists(queue_/"responses"/(id+".json"))) { fs::remove(entry.path()); continue; }
            try
            {
                if (entry.file_size() > 65536) throw std::runtime_error("Request exceeds 64 KiB");
                const Json request = ReadJson(entry.path());
                fs::remove(entry.path());
                if (request.at("id") != id) throw std::runtime_error("Request ID mismatch");
                const double now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
                if (request.at("expires_at").get<double>() < now) throw std::runtime_error("Request expired without execution");
                const std::string op = request.at("op");
                const Json args = request.value("args",Json::object());
                if (op == "inspect") { Respond(id,true,{{"document",document_},{"source",source_.string()},{"output",output_.string()},{"scene_path",scenePath_},{"catalog",catalog_},{"pending_action",pendingAction_},{"dirty",dirty_},{"busy",Busy()},{"status",status_},{"report",report_},{"enemy_types",EnemyTypes()},{"prefabs",prefabs_},{"preview_refresh_pending",structureDirty_},{"audio",AudioState()}}); continue; }
                if (op == "get_audio_memory") { Respond(id,true,AudioState()); continue; }
                if (op == "get_layout") { Respond(id,true,Layout()); continue; }
                if (op == "reset_layout") { engine_.ResetEditorLayout(); Respond(id,true,{{"scheduled",true}}); continue; }
                if (Busy()) throw std::runtime_error("Editor build is busy; inspect remains available");
                if (!pendingAction_.empty()) throw std::runtime_error("A level action needs attention; inspect remains available");
                if (op == "quit")
                {
                    if (dirty_ || audioDraft_ != audioConfig_) throw std::runtime_error("Unsaved scene or planning changes; save/apply or discard before closing");
                    Respond(id,true,{{"closing",true}}); engine_.RequestExit();
                }
                else if (op=="list_levels") { RefreshLevels(); Respond(id,true,catalog_); }
                else if (op=="open_level" || op=="create_level" || op=="duplicate_level" || op=="delete_level" || op=="update_level")
                {
                    Json action=args;
                    action["op"]=op=="open_level"?"open":op=="create_level"?"create":op=="duplicate_level"?"duplicate":op=="delete_level"?"delete":"update";
                    if (action.contains("discard")) throw std::runtime_error("Discard requires the editor's explicit unsaved-changes dialog");
                    if (op=="delete_level" && !action.value("confirmed",false)) throw std::runtime_error("Deleting a level requires confirmed: true; its saved source is archived");
                    QueueAction(action,id);
                }
                else if (op=="play_level" || op=="play_menu")
                {
                    if (audioDraft_!=audioConfig_) throw std::runtime_error("Apply or discard audio planning changes before playing");
                    const std::string preset=args.value("start_preset",std::string("default"));
                    bool found=preset=="default";
                    for (const auto& start : document_.value("test_starts",Json::array())) if (start.at("id")==preset) found=true;
                    if (!found) throw std::runtime_error("Unknown test start");
                    testStart_=preset; Save(true,id,op=="play_menu"?"menu":"level");
                }
                else if (op == "set_entity") { SetEntity(args); Respond(id,true,{{"entity",*Entity(args.at("id"))},{"dirty",true},{"validation","pending save"}}); }
                else if (op == "import_asset_pack") Respond(id,true,ImportAssetPack(args));
                else if (op == "add_prop") Respond(id,true,AddProp(args));
                else if (op == "add_enemy") Respond(id,true,AddEnemy(args));
                else if (op == "duplicate_enemy") Respond(id,true,DuplicateEnemy(args));
                else if (op == "add_waypoint") Respond(id,true,AddWaypoint(args));
                else if (op == "delete_entity") Respond(id,true,DeleteEntity(args));
                else if (op == "set_environment") { SetEnvironment(args.at("patch")); Respond(id,true,{{"environment",document_["environment"]},{"dirty",true},{"validation","pending save"}}); }
                else if (op == "set_material") { SetMaterial(args); Respond(id,true,{{"material",*Material(args.at("id"))},{"dirty",true},{"validation","pending save"}}); }
                else if (op == "set_preview_transform") { SetPreviewTransform(args); Respond(id,true,{{"entity",*Entity(args.at("id"))},{"dirty",dirty_}}); }
                else if (op == "set_audio_config")
                {
                    if (audioConfig_.empty()) throw std::runtime_error("Audio planning manifest is missing");
                    if (args.contains("patch") && audioDraft_ != audioConfig_)
                        throw std::runtime_error("Unapplied audio draft; Apply or Discard draft before a config patch");
                    std::string nextScenario = audioScenario_;
                    if (args.contains("scenario_id"))
                    {
                        const std::string scenario = args.at("scenario_id");
                        const auto& scenarios = audioReport_.at("scenarios");
                        if (!std::any_of(scenarios.begin(),scenarios.end(),[&](const Json& item) { return item.at("id")==scenario; }))
                            throw std::runtime_error("Unknown audio scenario");
                        nextScenario = scenario;
                    }
                    if (args.contains("patch"))
                    {
                        Json candidate = audioConfig_;
                        PatchAudio(candidate,args.at("patch"));
                        RecalculateAudio(candidate,true);
                    }
                    audioScenario_ = nextScenario;
                    Respond(id,true,AudioState());
                }
                else if (op == "recalculate_audio") { RecalculateAudio(ReadJson(root_/"content/audio_budget.json"),false); Respond(id,true,AudioState()); }
                else if (op == "save" || op == "build") Save(op == "build", id);
                else if (op == "capture")
                {
                    if (!captureId_.empty()) throw std::runtime_error("Capture already pending");
                    captureId_ = id; captureFrame_ = frame_ + 3;
                }
                else if (op == "reload")
                {
                    QueueAction({{"op","open"},{"file",source_.filename().string()}},id);
                }
                else throw std::runtime_error("Unknown operation");
            }
            catch (const std::exception& error)
            {
                Respond(id,false,{{"error",error.what()}});
                std::error_code ignored; fs::remove(entry.path(),ignored);
            }
        }
    }
    static ImU32 MarkerColor(const std::string& kind)
    {
        if (kind=="guard") return IM_COL32(239,90,75,255);
        if (kind=="spawn") return IM_COL32(67,212,225,255);
        if (kind=="objective") return IM_COL32(255,210,73,255);
        if (kind=="control") return IM_COL32(84,224,139,255);
        if (kind=="light") return IM_COL32(246,174,65,255);
        return IM_COL32(197,190,232,255);
    }
    void DrawLevels()
    {
        if (ImGui::Begin("Levels"))
        {
            ImGui::BeginDisabled(Busy() || !pendingAction_.empty());
            if (ImGui::Button("Refresh")) try { RefreshLevels(); } catch (const std::exception& e) { status_=e.what(); }
            ImGui::TextWrapped("Open a level to edit it. Checked levels appear in the game menu.");
            ImGui::BeginChild("Level list",ImVec2(0,230),ImGuiChildFlags_Borders);
            for (const auto& level : catalog_.at("levels"))
            {
                const std::string file=level.at("file"), title=LevelLabel(level.at("title"));
                const bool current=file==source_.filename().string();
                const std::string label=(current?"> ":"")+title+(current&&dirty_?" *":"")+"##"+file;
                if (ImGui::Selectable(label.c_str(),browserLevel_==file,ImGuiSelectableFlags_AllowDoubleClick))
                {
                    browserLevel_=file;
                    if (ImGui::IsMouseDoubleClicked(0)) try { QueueAction({{"op","open"},{"file",file}}); } catch (const std::exception& e) { status_=e.what(); }
                }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s",file.c_str());
            }
            ImGui::EndChild();
            const auto* selected=LevelEntry(browserLevel_);
            if (ImGui::Button("New level")) { newDuplicate_=false; showNew_=true; }
            ImGui::SameLine();
            ImGui::BeginDisabled(!selected);
            if (ImGui::Button("Duplicate")) { newDuplicate_=true; showNew_=true; }
            if (ImGui::Button("Open")) try { QueueAction({{"op","open"},{"file",browserLevel_}}); } catch (const std::exception& e) { status_=e.what(); }
            ImGui::SameLine(); if (ImGui::Button("Delete")) showDelete_=true;
            if (selected)
            {
                ImGui::TextWrapped("%s",browserLevel_.c_str());
                bool included=selected->at("in_bundle");
                if (ImGui::Checkbox("Include in game menu",&included))
                    try { QueueAction({{"op","update"},{"file",browserLevel_},{"in_bundle",included}}); } catch (const std::exception& e) { status_=e.what(); }
            }
            ImGui::EndDisabled();
            ImGui::Separator();
            ImGui::TextWrapped("Editing: %s",source_.filename().string().c_str());
            char title[81]{};
            const auto currentTitle=document_.at("title").get<std::string>();
            std::copy_n(currentTitle.data(),std::min(currentTitle.size(),sizeof(title)-1),title);
            ImGui::SetNextItemWidth(-1);
            if (ImGui::InputText("##Level title",title,sizeof(title))) { document_["title"]=title; dirty_=true; }
            ImGui::TextDisabled("Level title (Save + Cook to apply)");
            ImGui::EndDisabled();
        }
        ImGui::End();
        if (showNew_) { ImGui::OpenPopup("Create level"); showNew_=false; }
        if (ImGui::BeginPopupModal("Create level",nullptr,ImGuiWindowFlags_AlwaysAutoResize))
        {
            ImGui::TextUnformatted(newDuplicate_?"Duplicate the selected saved level":"Start with a small playable room");
            ImGui::InputText("Source name",newName_,sizeof(newName_));
            ImGui::InputText("Title",newTitle_,sizeof(newTitle_));
            ImGui::Checkbox("Include in game menu",&newInBundle_);
            if (ImGui::Button("Create"))
            {
                try
                {
                    Json action{{"op",newDuplicate_?"duplicate":"create"},{"name",newName_},{"title",newTitle_},{"in_bundle",newInBundle_}};
                    if (newDuplicate_) action["file"]=browserLevel_;
                    QueueAction(action); ImGui::CloseCurrentPopup();
                }
                catch (const std::exception& e) { status_=e.what(); }
            }
            ImGui::SameLine(); if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
            ImGui::EndPopup();
        }
        if (showDelete_) { ImGui::OpenPopup("Delete level?"); showDelete_=false; }
        if (ImGui::BeginPopupModal("Delete level?",nullptr,ImGuiWindowFlags_AlwaysAutoResize))
        {
            ImGui::Text("Delete %s?",browserLevel_.c_str());
            ImGui::TextUnformatted("Remove this level from the project and game menu.\nA recoverable copy goes to .dev/editor/deleted-levels.\nShared models and textures are kept.");
            if (ImGui::Button("Delete level"))
                try { QueueAction({{"op","delete"},{"file",browserLevel_}}); ImGui::CloseCurrentPopup(); } catch (const std::exception& e) { status_=e.what(); }
            ImGui::SameLine(); if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
            ImGui::EndPopup();
        }
        if (showUnsaved_) { ImGui::OpenPopup("Unsaved changes"); showUnsaved_=false; }
        if (ImGui::BeginPopupModal("Unsaved changes",nullptr,ImGuiWindowFlags_AlwaysAutoResize))
        {
            ImGui::Text("Save changes to %s before continuing?",source_.filename().string().c_str());
            if (audioDraft_!=audioConfig_) ImGui::TextUnformatted("Audio planning changes will also be applied or discarded.");
            if (ImGui::Button("Save and continue"))
            {
                try
                {
                    if (audioDraft_!=audioConfig_) RecalculateAudio(audioDraft_,true);
                    Save(false); continueAfterSave_=true; ImGui::CloseCurrentPopup();
                }
                catch (const std::exception& e) { status_=e.what(); }
            }
            ImGui::SameLine();
            if (ImGui::Button("Discard and continue")) { pendingAction_["discard"]=true; actionReady_=true; ImGui::CloseCurrentPopup(); }
            ImGui::SameLine();
            if (ImGui::Button("Cancel")) { pendingAction_=Json::object(); actionRequestId_.clear(); ImGui::CloseCurrentPopup(); }
            ImGui::EndPopup();
        }
    }
    void DrawObjects()
    {
        if (ImGui::Begin("Room Objects"))
        {
            ImGui::BeginDisabled(Busy());
            if (ImGui::Button("Add enemy"))
                try { AddEnemy(Json::object()); }
                catch (const std::exception& error) { status_ = error.what(); }
            ImGui::SameLine();
            if (ImGui::Button("Add waypoint"))
                try { AddWaypoint(Json::object()); }
                catch (const std::exception& error) { status_ = error.what(); }
            if (const auto* selected = Entity(selected_))
            {
                const bool enemy = selected->at("kind") == "guard";
                const bool point = selected->at("kind") == "waypoint";
                const bool prop = DeletableProp(*selected);
                if (enemy && ImGui::Button("Duplicate enemy"))
                    try { DuplicateEnemy({{"id",selected_}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
                if (enemy) ImGui::SameLine();
                if ((enemy || point || prop) && ImGui::Button(enemy ? "Delete enemy" : point ? "Delete waypoint" : "Delete prop"))
                    try { DeleteEntity({{"id",selected_}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
            }
            ImGui::EndDisabled();
            if (structureDirty_) ImGui::TextWrapped("Viewport refresh pending: Save + Cook to show added or removed models and waypoint markers.");
            ImGui::TextWrapped("New enemies start at an existing actor/waypoint position. Move their XYZ placement before playing. Duplicates get independent route points.");
            if (ImGui::CollapsingHeader("Asset packs / props",ImGuiTreeNodeFlags_DefaultOpen))
            {
                ImGui::BeginDisabled(Busy());
                ImGui::InputText("Pack URI",assetPackUri_,sizeof(assetPackUri_));
                if (ImGui::Button("Import asset pack"))
                    try { ImportAssetPack({{"uri",std::string(assetPackUri_)}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
                if (ImGui::BeginCombo("Prefab",selectedPrefab_.empty() ? "Import a pack" : selectedPrefab_.c_str()))
                {
                    for (const auto& prefab : prefabs_)
                    {
                        const std::string id = prefab.at("id");
                        if (ImGui::Selectable(id.c_str(),selectedPrefab_ == id)) selectedPrefab_ = id;
                    }
                    ImGui::EndCombo();
                }
                ImGui::BeginDisabled(selectedPrefab_.empty());
                ImGui::Checkbox("Highlight new prop",&highlightNewProp_);
                if (ImGui::Button("Place prop"))
                    try { AddProp({{"prefab",selectedPrefab_},{"loot_highlight",highlightNewProp_}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
                ImGui::EndDisabled();
                ImGui::EndDisabled();
                ImGui::TextWrapped("URIs start inside content/. Props are decorative models with no collision or pickup behavior. Highlight adds a gentle brightness pulse in the game for loot that should stand out. Set placement in Object Properties, then Save + Cook. Import the pack again after reopening to restore its prefab list; placed objects are saved with the level.");
            }
            ImGui::Separator();
            for (const auto& e:document_["entities"])
            {
                const std::string id=e["id"],kind=e["kind"];
                ImGui::PushStyleColor(ImGuiCol_Text,MarkerColor(kind));
                if (ImGui::Selectable((id+"##object").c_str(),id==selected_)) selected_=id;
                ImGui::PopStyleColor();
            }
        }
        ImGui::End();
    }
    void DrawEnemy(std::string id)
    {
        // A local snapshot stays valid when Add waypoint grows the object array.
        Json enemy = *Entity(id);
        const std::string typeId = enemy.value("enemy_type",std::string("watchman"));
        const Json type = EnemyType(typeId);
        if (ImGui::BeginCombo("Enemy type",type.at("label").get<std::string>().c_str()))
        {
            for (const auto& candidate : EnemyTypes())
            {
                const std::string candidateId = candidate.at("id"), label = candidate.at("label");
                if (ImGui::Selectable(label.c_str(),typeId == candidateId))
                    try { SetEntity({{"id",id},{"patch",{{"enemy_type",candidateId}}}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
            }
            ImGui::EndCombo();
        }
        enemy = *Entity(id);
        const std::string behavior = enemy.value("behavior",std::string("patrol"));
        Json route = enemy.value("patrol",Json::array());
        if (ImGui::BeginCombo("Behavior",behavior == "sentry" ? "Sentry (hold / return to post)" : "Patrol (loop route)"))
        {
            for (const char* option : {"sentry","patrol"})
            {
                ImGui::BeginDisabled(std::string(option) == "patrol" && route.size() < 2);
                if (ImGui::Selectable(option,behavior == option))
                    try { SetEntity({{"id",id},{"patch",{{"behavior",option}}}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
                ImGui::EndDisabled();
            }
            ImGui::EndCombo();
        }
        ImGui::TextWrapped("Sentries face their authored Y rotation and return to their post after investigating. Patrol requires at least two points. Both behaviors can hear and pursue the player.");
        const Json currentType = EnemyType(Entity(id)->value("enemy_type",std::string("watchman")));
        for (const char* key : {"speed","sight_range","hearing_range"})
        {
            const auto* current = Entity(id);
            const bool overridden = current->contains(key), speed = std::string(key) == "speed";
            float value = overridden ? current->at(key).get<float>() : currentType.at(key).get<float>();
            ImGui::PushID(key);
            if (ImGui::DragFloat(key,&value,.02f,speed?.05f:.1f,speed?4.f:128.f,"%.2f",ImGuiSliderFlags_AlwaysClamp))
                try { SetEntity({{"id",id},{"patch",{{key,value}}}}); }
                catch (const std::exception& error) { status_ = error.what(); }
            if (overridden)
            {
                ImGui::SameLine();
                if (ImGui::SmallButton("Use type default"))
                    try { SetEntity({{"id",id},{"patch",{{key,nullptr}}}}); }
                    catch (const std::exception& error) { status_ = error.what(); }
            }
            else { ImGui::SameLine(); ImGui::TextDisabled("type default"); }
            ImGui::PopID();
        }
        ImGui::TextWrapped("Changing type keeps explicit per-enemy overrides. Use type default to inherit future code tuning. Both initial types share the placeholder guard model.");
        ImGui::Separator();
        ImGui::Text("Patrol route (%zu / 32 points)",route.size());
        for (size_t index = 0; index < route.size(); ++index)
        {
            ImGui::PushID(static_cast<int>(index));
            const std::string pointId = route[index];
            if (ImGui::Selectable((std::to_string(index + 1) + ". " + pointId).c_str())) selected_ = pointId;
            bool changed = false;
            if (index > 0 && ImGui::SmallButton("Up")) { std::swap(route[index],route[index - 1]); changed = true; }
            if (index > 0) ImGui::SameLine();
            if (index + 1 < route.size() && ImGui::SmallButton("Down")) { std::swap(route[index],route[index + 1]); changed = true; }
            if (index + 1 < route.size()) ImGui::SameLine();
            if (ImGui::SmallButton("Remove from route")) { route.erase(route.begin() + index); changed = true; }
            ImGui::PopID();
            if (changed)
            {
                Json patch = {{"patrol",route}};
                if (route.size() < 2) patch["behavior"] = "sentry";
                try
                {
                    SetEntity({{"id",id},{"patch",patch}});
                    if (route.size() < 2) status_ = "Route has fewer than two points; enemy changed to Sentry so the level remains valid.";
                }
                catch (const std::exception& error) { status_ = error.what(); }
                break;
            }
        }
        ImGui::BeginDisabled(route.size() >= 32);
        if (ImGui::BeginCombo("Append existing waypoint","Choose waypoint..."))
        {
            for (const auto& candidate : document_.at("entities"))
                if (candidate.at("kind") == "waypoint")
                {
                    const std::string pointId = candidate.at("id");
                    if (ImGui::Selectable(pointId.c_str()))
                    {
                        route.push_back(pointId);
                        try { SetEntity({{"id",id},{"patch",{{"patrol",route}}}}); }
                        catch (const std::exception& error) { status_ = error.what(); }
                    }
                }
            ImGui::EndCombo();
        }
        if (ImGui::Button("Create + append waypoint"))
        {
            try
            {
                Json position = Entity(id)->at("transform").at("position");
                if (!route.empty()) position = Entity(route.back().get<std::string>())->at("transform").at("position");
                const Json result = AddWaypoint({{"position",position}});
                route.push_back(result.at("entity").at("id"));
                SetEntity({{"id",id},{"patch",{{"patrol",route}}}});
            }
            catch (const std::exception& error) { status_ = error.what(); }
        }
        ImGui::EndDisabled();
        ImGui::TextWrapped("Select a route point above to edit XYZ. Save + Cook adds its viewport marker; use Entity List to select its gizmo. Points are visited in order, then the route loops. Keep each segment clear of walls: navigation currently follows straight segments.");
        ImGui::TextWrapped("Existing waypoints may be shared by several routes. Editing one moves it for all users. Removing a route entry keeps the waypoint object; Delete waypoint refuses points still in use.");
    }
    void DrawProperties()
    {
        if (ImGui::Begin("Object Properties"))
        {
            ImGui::TextWrapped("XYZ transforms and stock viewport gizmos update canonical content. Rotation uses Euler XYZ degrees; up is Y. Save + Cook validates collision and refreshes assets.");
            DrawEnvironment();
            ImGui::Separator();
            if (auto* entity=Entity(selected_))
            {
                ImGui::TextUnformatted(selected_.c_str());
                ImGui::TextDisabled("Kind: %s",(*entity)["kind"].get<std::string>().c_str());
                ImGui::BeginDisabled(Busy());
                for (const char* name : {"position","rotation","scale"})
                {
                    const auto& value = (*entity)["transform"][name];
                    float xyz[3] = {value[0],value[1],value[2]};
                    const bool rotation = std::string(name)=="rotation", scale = std::string(name)=="scale";
                    if (ImGui::DragFloat3(name,xyz,rotation?.25f:.02f,scale?.001f:rotation?-3600.f:-1024.f,rotation?3600.f:1024.f,"%.3f"))
                    {
                        try { SetEntity({{"id",selected_},{"patch",{{"transform",{{name,{xyz[0],xyz[1],xyz[2]}}}}}}}); }
                        catch (const std::exception& error) { status_=error.what(); }
                    }
                }
                if (entity->contains("model")) ImGui::TextDisabled("Model: %s",(*entity)["model"].get<std::string>().c_str());
                if (DeletableProp(*entity))
                {
                    bool highlighted = entity->value("loot_highlight",false);
                    if (ImGui::Checkbox("Loot highlight",&highlighted))
                        try { SetEntity({{"id",selected_},{"patch",{{"loot_highlight",highlighted}}}}); }
                        catch (const std::exception& error) { status_=error.what(); }
                    ImGui::TextWrapped("A gentle brightness pulse helps loot stand out in the game. It adds no light source or pickup behavior. Save + Cook and launch the game to preview it.");
                }
                if (entity->contains("collider"))
                    ImGui::TextWrapped("Collision: upright box follows position, yaw and scale. Pitch/roll require a separate upright proxy and are rejected by the current cook.");
                for (const char* name:{"radius","intensity"})
                    if (entity->contains(name))
                    {
                        float value=(*entity)[name];
                        const bool intensity = std::string(name) == "intensity";
                        if (ImGui::DragFloat(name,&value,.02f,0,intensity?16.f:128.f,"%.2f",intensity?ImGuiSliderFlags_AlwaysClamp:0))
                        {
                            try { SetEntity({{"id",selected_},{"patch",{{name,value}}}}); }
                            catch (const std::exception& error) { status_=error.what(); }
                        }
                    }
                if (entity->at("kind") == "light")
                {
                    const Json value = entity->value("color",Json::array({1,.75,.4}));
                    float rgb[3] = {value[0],value[1],value[2]};
                    if (ImGui::ColorEdit3("Light RGB",rgb))
                        try { SetEntity({{"id",selected_},{"patch",{{"color",{rgb[0],rgb[1],rgb[2]}}}}}); }
                        catch (const std::exception& error) { status_=error.what(); }
                    ImGui::TextWrapped("RGB and intensity illuminate nearby surfaces and affect the night scene's stealth visibility. Save + Cook refreshes the preview.");
                }
                if (entity->contains("material")) DrawMaterial(entity->at("material").get<std::string>());
                if (entity->contains("target"))
                {
                    const auto target=(*entity)["target"].get<std::string>();
                    if (ImGui::BeginCombo("Linked door",target.c_str()))
                    {
                        for (const auto& candidate:document_["entities"])
                            if (candidate["kind"]=="door")
                            {
                                const auto id=candidate["id"].get<std::string>();
                                if (ImGui::Selectable(id.c_str(),id==target)) { (*entity)["target"]=id; dirty_=true; }
                            }
                        ImGui::EndCombo();
                    }
                }
                if (entity->at("kind") == "guard") DrawEnemy(selected_);
                ImGui::EndDisabled();
            }
        }
        ImGui::End();
    }
    void DrawEnvironment()
    {
        if (!ImGui::CollapsingHeader("Night environment")) return;
        ImGui::BeginDisabled(Busy());
        if (!document_.contains("environment"))
        {
            ImGui::TextWrapped("This scene uses the original playable's lighting. Add an environment to author colored moonlight, fog and sky.");
            if (ImGui::Button("Add night environment")) SetEnvironment(Json::object());
        }
        else
        {
            const Json environment = document_.at("environment");
            for (const auto& [key,label] : std::vector<std::pair<const char*,const char*>>{
                {"ambient","Shadow fill RGB"},{"moon_color","Moon RGB"},{"fog_color","Distant fog RGB"},
                {"sky_top","Upper sky RGB"},{"sky_bottom","Horizon RGB"}})
            {
                const auto& value = environment.at(key);
                float rgb[3] = {value[0],value[1],value[2]};
                if (ImGui::ColorEdit3(label,rgb))
                    try { SetEnvironment({{key,{rgb[0],rgb[1],rgb[2]}}}); }
                    catch (const std::exception& error) { status_=error.what(); }
            }
            const auto& direction = environment.at("moon_direction");
            float xyz[3] = {direction[0],direction[1],direction[2]};
            if (ImGui::DragFloat3("Direction toward moon",xyz,.01f,-1,1,"%.2f",ImGuiSliderFlags_AlwaysClamp))
                try { SetEnvironment({{"moon_direction",{xyz[0],xyz[1],xyz[2]}}}); }
                catch (const std::exception& error) { status_=error.what(); }
            for (const auto& [key,label] : std::vector<std::pair<const char*,const char*>>{
                {"moon_intensity","Moon intensity"},{"exposure","Exposure"},
                {"fog_near","Fog starts (m)"},{"fog_far","Fog full (m)"}})
            {
                float value = environment.at(key);
                const std::string field = key;
                const float minimum = field == "exposure" ? .05f : 0;
                const float maximum = field == "moon_intensity" ? 16 : field == "exposure" ? 8 : 1024;
                if (ImGui::DragFloat(label,&value,.02f,minimum,maximum,"%.2f",ImGuiSliderFlags_AlwaysClamp))
                    try { SetEnvironment({{key,value}}); }
                    catch (const std::exception& error) { status_=error.what(); }
            }
            ImGui::TextWrapped("Keep shadow fill low and shape visibility with moonlit faces and warm lamps. Roofs and walls block both moonlight and point lights in the game. Exposure brightens the image without changing guard perception.");
            ImGui::TextWrapped("Save + Cook updates the desktop preview. Build + Play is the reference for N64 lighting, fog and contrast.");
        }
        ImGui::EndDisabled();
    }
    void DrawMaterial(const std::string& id)
    {
        if (!ImGui::CollapsingHeader("Material")) return;
        auto* material = Material(id);
        if (!material) return;
        ImGui::TextWrapped("%s (shared by objects using this material)",id.c_str());
        const auto& color = material->at("color");
        float rgba[4] = {color[0],color[1],color[2],color[3]};
        if (ImGui::ColorEdit4("Surface RGBA",rgba))
            try { SetMaterial({{"id",id},{"patch",{{"color",{rgba[0],rgba[1],rgba[2],rgba[3]}}}}}); }
            catch (const std::exception& error) { status_=error.what(); }
        const Json emission = material->value("emissive",Json::array({0,0,0}));
        float rgb[3] = {emission[0],emission[1],emission[2]};
        if (ImGui::ColorEdit3("Emission RGB",rgb))
            try { SetMaterial({{"id",id},{"patch",{{"emissive",{rgb[0],rgb[1],rgb[2]}}}}}); }
            catch (const std::exception& error) { status_=error.what(); }
        ImGui::TextWrapped("Emission keeps a lamp or window visible in shadow. Add a light object to illuminate nearby geometry and affect guard perception.");
        if (material->contains("texture"))
        {
            Json texture = material->at("texture");
            ImGui::TextWrapped("Texture: %s",texture.at("uri").get<std::string>().c_str());
            for (const auto& [key,label] : std::vector<std::pair<const char*,const char*>>{{"width","Cooked width"},{"height","Cooked height"}})
            {
                int size = texture.at(key);
                if (ImGui::BeginCombo(label,std::to_string(size).c_str()))
                {
                    for (int option : {8,16,32,64})
                        if (ImGui::Selectable(std::to_string(option).c_str(),size==option))
                        {
                            texture[key]=option;
                            try { SetMaterial({{"id",id},{"patch",{{"texture",texture}}}}); }
                            catch (const std::exception& error) { status_=error.what(); }
                        }
                    ImGui::EndCombo();
                }
            }
            ImGui::Text("RGBA16 tile: %d / 4096 bytes",material->at("texture").at("width").get<int>()*material->at("texture").at("height").get<int>()*2);
        }
    }
    void DrawBuild()
    {
        if (ImGui::Begin("Build & Diagnostics"))
        {
            ImGui::BeginDisabled(Busy());
            if (ImGui::Button("Save + Cook")) Save(false);
            ImGui::SameLine(); if (ImGui::Button("Play level")) Save(true,{},"level");
            ImGui::SameLine(); if (ImGui::Button("Play game menu")) Save(true,{},"menu");
            std::string startLabel="Default spawn";
            for (const auto& start : document_.value("test_starts",Json::array()))
                if (start.at("id")==testStart_) startLabel=start.value("label",testStart_);
            if (ImGui::BeginCombo("Test start",startLabel.c_str()))
            {
                if (ImGui::Selectable("Default spawn",testStart_=="default")) testStart_="default";
                for (const auto& start : document_.value("test_starts",Json::array()))
                {
                    const std::string id=start.at("id");
                    if (ImGui::Selectable(start.value("label",id).c_str(),testStart_==id)) testStart_=id;
                }
                ImGui::EndCombo();
            }
            if (ImGui::Button("Build ROM only")) Save(true);
            ImGui::EndDisabled();
            ImGui::SameLine(); if (ImGui::Button("Reset Layout")) engine_.ResetEditorLayout();
            ImGui::SameLine(); ImGui::TextUnformatted(Busy()?"Working...":dirty_?"Unsaved changes":"Saved");
            ImGui::TextWrapped("%s",status_.c_str());
            ImGui::TextWrapped("Level: %s",source_.filename().string().c_str());
            if (!report_.empty())
            {
                const Json counts = report_.value("counts",Json::object());
                ImGui::Text("Cook %.2f ms | %d models | %d triangles | %d geometry bytes",report_.value("elapsed_ms",0.0),counts.value("models",0),counts.value("instanced_triangles",0),report_.value("compiled_geometry_bytes",0));
                ImGui::TextWrapped("Play saves this level before launching Ares. Play game menu bundles the saved levels checked in Levels. Audio Memory is a separate planning estimate.");
            }
            if (ImGui::CollapsingHeader("Last compiler log")) ImGui::TextUnformatted(Tail(queue_/"compiler.log").c_str());
            if (ImGui::CollapsingHeader("Last ROM build log")) ImGui::TextUnformatted(Tail(queue_/"rom-build.log").c_str());
        }
        ImGui::End();
    }
    static void AudioInteger(Json& object, const char* key, const char* label)
    {
        int value = object.value(key,0);
        if (ImGui::InputInt(label,&value)) object[key] = value;
    }
    static void AudioKiB(Json& object, const char* key, const char* label)
    {
        float value = object.contains(key) && object[key].is_number() ? object[key].get<float>()/1024.f : 0;
        if (ImGui::DragFloat(label,&value,.25f,0,8192,"%.2f KiB",ImGuiSliderFlags_AlwaysClamp))
            object[key] = static_cast<int64_t>(std::llround(value*1024.0));
    }
    void DrawAudio()
    {
        if (ImGui::Begin("Audio Memory"))
        {
            ImGui::TextColored(ImVec4(1,.76f,.3f,1),"PLANNING ONLY - no runtime measurements");
            ImGui::TextWrapped("The 256 KiB starting envelope is illustrative, not an approved project allocation. RAM totals include retained pools, overlap and explicit allowances. ROM storage is separate.");
            if (audioReport_.contains("scenarios"))
            {
                const auto& scenarios = audioReport_["scenarios"];
                std::string label = audioScenario_;
                for (const auto& item : scenarios) if (item.at("id")==audioScenario_) label=item.value("label",audioScenario_);
                if (ImGui::BeginCombo("Scenario",label.c_str()))
                {
                    for (const auto& item : scenarios)
                    {
                        const std::string id=item.at("id"), text=item.value("label",id);
                        if (ImGui::Selectable((text+"##"+id).c_str(),id==audioScenario_)) audioScenario_=id;
                    }
                    ImGui::EndCombo();
                }
                for (const auto& item : scenarios) if (item.at("id")==audioScenario_)
                {
                    const double total=item.value("total_ram_bytes",0.0);
                    const double budget=item.contains("budget_bytes") && item["budget_bytes"].is_number()?item["budget_bytes"].get<double>():0;
                    ImGui::Text("RAM estimate %.2f KiB | envelope %.2f KiB",total/1024,budget/1024);
                    if (budget>0) ImGui::Text("Allowance remaining: %.2f KiB",(budget-total)/1024);
                    else ImGui::TextUnformatted("No RAM envelope assigned");
                    ImGui::ProgressBar(static_cast<float>(budget>0?std::clamp(total/budget,0.0,1.0):0),ImVec2(-1,0),"Costs and allowances / illustrative envelope");
                    ImGui::Text("Mixer channels in scenario: %d",item.value("mixer_channels",0));
                    const std::string state=item.value("conditional_budget_state",item.value("budget_status",std::string("unverified")));
                    ImGui::TextWrapped("Estimate state: %s. Positive headroom is not proof that runtime fits.",state.c_str());
                    if (ImGui::BeginTable("Audio breakdown",2,ImGuiTableFlags_RowBg|ImGuiTableFlags_BordersInnerH))
                    {
                        ImGui::TableSetupColumn("RAM component"); ImGui::TableSetupColumn("KiB"); ImGui::TableHeadersRow();
                        for (const auto& [name,value] : item.at("components").items())
                        {
                            ImGui::TableNextRow(); ImGui::TableNextColumn();
                            std::string text=name; std::replace(text.begin(),text.end(),'_',' ');
                            ImGui::TextUnformatted(text.c_str()); ImGui::TableNextColumn(); ImGui::Text("%.2f",value.get<double>()/1024);
                        }
                        ImGui::EndTable();
                    }
                    const auto coverage=item.value("coverage",Json::object());
                    if (coverage.contains("unmeasured_items"))
                    {
                        ImGui::TextColored(ImVec4(1,.76f,.3f,1),"Unmeasured / unknown costs");
                        for (const auto& unknown : coverage["unmeasured_items"])
                        {
                            const auto text=unknown.is_string()?unknown.get<std::string>():unknown.dump();
                            ImGui::TextWrapped("- %s",text.c_str());
                        }
                    }
                }
                const auto summary=audioReport_.value("summary",Json::object());
                ImGui::Separator();
                ImGui::Text("ROM payload %.2f KiB | container overhead %.2f KiB",summary.value("rom_encoded_payload_bytes",0.0)/1024,summary.value("rom_container_overhead_bytes",0.0)/1024);
                ImGui::Text("Peak RAM estimate %.2f KiB (%s)",summary.value("peak_ram_bytes",0.0)/1024,summary.value("peak_scenario_id",std::string()).c_str());
            }
            else ImGui::TextWrapped("No audio estimate available. Build the editor or recalculate a saved planning manifest.");
            if (!audioDraft_.empty() && ImGui::CollapsingHeader("Adjust planning assumptions"))
            {
                ImGui::BeginDisabled(Busy());
                AudioInteger(audioDraft_["output"],"sample_rate_hz","Output rate (Hz)");
                AudioInteger(audioDraft_["output"],"buffer_frames","Stereo frames per buffer");
                AudioInteger(audioDraft_["output"],"buffer_count","Output buffer count");
                const double rate = audioDraft_["output"].value("sample_rate_hz",0.0);
                const double frames = audioDraft_["output"].value("buffer_frames",0.0);
                const double count = audioDraft_["output"].value("buffer_count",0.0);
                if (rate > 0) ImGui::Text("Buffer duration %.2f ms | allocated capacity %.2f ms",1000*frames/rate,1000*frames*count/rate);
                ImGui::TextWrapped("With fixed frames, changing output Hz changes duration, not bytes. Base payload is frames x 4 x count; the report adds padding/alignment. Actual queued latency must be measured.");
                bool assignBudget = !audioDraft_["budget"].is_null();
                if (ImGui::Checkbox("Assign an illustrative RAM envelope",&assignBudget))
                    audioDraft_["budget"] = assignBudget ? Json{{"ram_bytes",262144},{"status","illustrative"},{"note","Editable planning envelope; not an approved project allocation."}} : Json(nullptr);
                ImGui::BeginDisabled(!assignBudget);
                AudioKiB(audioDraft_["budget"],"ram_bytes","Illustrative RAM envelope");
                ImGui::EndDisabled();
                AudioKiB(audioDraft_["buffer_pool"],"decoded_bytes","Retained decoded pool");
                AudioKiB(audioDraft_["buffer_pool"],"decoder_workspace_bytes","Retained decoder workspace");
                AudioInteger(audioDraft_["buffer_pool"],"channel_capacity","Provisioned mixer channels");
                AudioKiB(audioDraft_["allowances"],"runtime_bytes","Runtime metadata allowance");
                AudioKiB(audioDraft_["allowances"],"allocator_bytes","Allocator allowance");
                AudioKiB(audioDraft_["allowances"],"unmeasured_bytes","Unmeasured-cost allowance");
                AudioKiB(audioDraft_["allowances"],"reserve_bytes","Reserve");
                ImGui::TextWrapped("Output rate does not automatically resize authored voice windows. Pools are explicit provisioning assumptions. Apply saves this planning manifest; it does not allocate game memory.");
                if (ImGui::Button("Apply + Recalculate"))
                    try { RecalculateAudio(audioDraft_,true); } catch (const std::exception& error) { audioStatus_=error.what(); }
                ImGui::SameLine();
                if (ImGui::Button("Discard draft")) audioDraft_=audioConfig_;
                ImGui::EndDisabled();
                if (audioDraft_!=audioConfig_) ImGui::TextUnformatted("Unapplied planning changes");
            }
            ImGui::BeginDisabled(Busy() || audioDraft_ != audioConfig_);
            if (ImGui::Button("Recalculate saved plan"))
                try { RecalculateAudio(ReadJson(root_/"content/audio_budget.json"),false); } catch (const std::exception& error) { audioStatus_=error.what(); }
            ImGui::EndDisabled();
            ImGui::TextWrapped("%s",audioStatus_.c_str());
        }
        ImGui::End();
    }
    void Draw()
    {
        DrawLevels();
        ImGui::BeginDisabled(!pendingAction_.empty());
        DrawObjects(); DrawProperties(); DrawBuild(); DrawAudio();
        ImGui::EndDisabled();
        // Startup warm-up frames can precede custom panel creation. Select our
        // authoring tabs once they exist, only after a default/reset layout.
        if (focusProjectTabs)
        {
            for (const char* name : {"Levels","Room Objects","Object Properties","Build & Diagnostics","Audio Memory"})
                if (auto* window = ImGui::FindWindowByName(name); window && window->DockNode)
                {
                    window->DockNode->SelectedTabId = window->TabId;
                    if (auto* tabs = window->DockNode->TabBar)
                        tabs->SelectedTabId = tabs->NextSelectedTabId = window->TabId;
                }
            focusProjectTabs = false;
            ImGui::MarkIniSettingsDirty();
        }
    }
};
} // namespace

int main(int argc, char** argv)
{
    try
    {
        fs::path root=argc>1?fs::absolute(argv[1]):fs::current_path();
        while (!fs::exists(root/"tools/compile_level.py"))
        {
            if (root==root.parent_path()) throw std::runtime_error("Pass the DarkLantern64 repository directory as the first argument");
            root=root.parent_path();
        }
        fs::current_path(root);
        const fs::path source = argc>2 ? fs::absolute(argv[2]) : root/"content/first_room.json";
        DemoEditor editor(root,source,argc>2);
        editor.Initialize();
        editor.Loop();
        return 0;
    }
    catch (const std::exception& error)
    {
        MessageBoxA(nullptr,error.what(),"DarkLantern64 editor",MB_OK|MB_ICONERROR);
        return 1;
    }
}
