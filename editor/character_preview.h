#pragma once

#include <animation.h>
#include <nlohmann/json.hpp>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

// Owns the cooked integer keys and exposes the same immutable C asset that the
// N64 sampler consumes. Nonmovable: its C pointers refer to the owned vectors.
class CharacterPreviewAsset
{
    using Json = nlohmann::json;
    std::string id_;
    std::vector<std::string> boneNames_, clipNames_;
    std::vector<DlAnimBone> bones_;
    std::vector<uint8_t> vertexBones_;
    std::vector<DlAnimClip> clips_;
    std::vector<std::vector<DlAnimTrack>> tracks_;
    std::vector<std::vector<std::vector<int16_t>>> values_;
    std::vector<std::vector<DlAnimEvent>> events_;
    std::vector<std::string> socketNames_;
    std::vector<DlAnimSocket> sockets_;

    static int Integer(const Json& value, int low, int high)
    {
        if (!value.is_number_integer() || value.get<int64_t>() < low || value.get<int64_t>() > high)
            throw std::runtime_error("Cooked character integer is outside its supported range");
        return value.get<int>();
    }
    static float Number(const Json& value)
    {
        if (!value.is_number() || !std::isfinite(value.get<float>()))
            throw std::runtime_error("Cooked character contains a non-finite number");
        return value.get<float>();
    }
    static void Array(const Json& value, size_t low, size_t high)
    {
        if (!value.is_array() || value.size() < low || value.size() > high)
            throw std::runtime_error("Cooked character array has an unsupported size");
    }
    static DlVec3 Vector(const Json& value)
    {
        Array(value, 3, 3);
        return {Number(value[0]), Number(value[1]), Number(value[2])};
    }
    static DlAnimTransform Transform(const Json& value)
    {
        const auto& q = value.at("rotation");
        Array(q, 4, 4);
        return {Vector(value.at("translation")), {Number(q[0]), Number(q[1]), Number(q[2]), Number(q[3])}};
    }

public:
    struct JointMesh { int joint; std::string uri; };
    // Cooked N64/glTF UVs use top-origin V. LightEngine's static glTF importer
    // flips V before native Vertex upload; direct dynamic meshes must do the
    // same conversion because they bypass that importer.
    static DlVec2 ToLightEngineUv(DlVec2 target) { return {target.u,1.f-target.v}; }
    DlAnimationAsset asset{};
    std::vector<JointMesh> meshes;
    int crossJointTriangles = 0;
    std::string sourceHash, topologyHash;
    std::vector<DlVec3> bindPositions, bindNormals;
    std::vector<DlVec2> bindUvs;
    std::vector<uint32_t> indices;

    explicit CharacterPreviewAsset(const Json& source)
    {
        if (source.at("schema_version") != 1) throw std::runtime_error("Unsupported cooked character preview version");
        const auto& joints = source.at("bones");
        const auto& clips = source.at("clips");
        const auto& vertexBones = source.at("vertex_bones");
        Array(joints, 1, DL_ANIMATION_MAX_BONES);
        Array(clips, 1, 64);
        Array(vertexBones, 1, 65535);
        id_ = source.at("id").get<std::string>();
        boneNames_.resize(joints.size()); bones_.resize(joints.size());
        for (size_t i = 0; i < joints.size(); ++i)
        {
            const auto& bone = joints[i];
            boneNames_[i] = bone.at("name").get<std::string>();
            bones_[i] = {boneNames_[i].c_str(), static_cast<int8_t>(Integer(bone.at("parent"), -1, static_cast<int>(i)-1)),
                         Transform(bone.at("rest")), Transform(bone.at("inverse_bind"))};
        }
        for (const auto& value : vertexBones) vertexBones_.push_back(static_cast<uint8_t>(Integer(value, 0, static_cast<int>(joints.size())-1)));
        clipNames_.resize(clips.size()); clips_.resize(clips.size()); tracks_.resize(clips.size());
        values_.resize(clips.size()); events_.resize(clips.size());
        for (size_t i = 0; i < clips.size(); ++i)
        {
            const auto& clip = clips[i];
            const auto& tracks = clip.at("tracks");
            const auto& events = clip.at("events");
            Array(tracks, 0, joints.size()*2); Array(events, 0, 256);
            const int samples = Integer(clip.at("sample_count"), 2, 4096);
            tracks_[i].resize(tracks.size()); values_[i].resize(tracks.size());
            for (size_t j = 0; j < tracks.size(); ++j)
            {
                const auto& track = tracks[j];
                const auto channel = track.at("channel").get<std::string>();
                if (channel != "rotation" && channel != "translation") throw std::runtime_error("Unsupported animation channel");
                const int count = Integer(track.at("sample_count"), 1, samples);
                if (count != 1 && count != samples) throw std::runtime_error("Animation keys must be constant or uniform");
                const int width = channel == "rotation" ? 4 : 3;
                const auto& values = track.at("values");
                Array(values, count*width, count*width);
                for (const auto& value : values) values_[i][j].push_back(static_cast<int16_t>(Integer(value, -32767, 32767)));
                tracks_[i][j] = {static_cast<uint8_t>(Integer(track.at("bone"), 0, static_cast<int>(joints.size())-1)),
                    static_cast<uint8_t>(channel == "rotation" ? DL_ANIM_ROTATION : DL_ANIM_TRANSLATION),
                    static_cast<uint16_t>(count), values_[i][j].data()};
            }
            for (const auto& event : events)
            {
                const auto kind = event.at("kind").get<std::string>();
                if (kind != "foot_left" && kind != "foot_right") throw std::runtime_error("Unsupported character event");
                events_[i].push_back({static_cast<uint16_t>(Integer(event.at("phase"), 0, 65535)),
                    static_cast<uint8_t>(kind == "foot_left" ? DL_ANIM_FOOT_LEFT : DL_ANIM_FOOT_RIGHT)});
            }
            clipNames_[i] = clip.at("id").get<std::string>();
            clips_[i] = {clipNames_[i].c_str(), Number(clip.at("duration")), Number(clip.at("stride_length")),
                Number(clip.at("translation_scale")), static_cast<uint16_t>(samples), static_cast<uint16_t>(tracks.size()),
                clip.at("loop").get<bool>(), tracks_[i].data(), events_[i].data(), static_cast<uint16_t>(events.size())};
        }
        asset = {id_.c_str(), bones_.data(), static_cast<uint16_t>(bones_.size()), vertexBones_.data(), clips_.data(),
            static_cast<uint16_t>(clips_.size()), static_cast<int16_t>(Integer(source.at("head_bone"), -1, static_cast<int>(joints.size())-1)),
            Vector(source.at("bounds_center")), Number(source.at("bounds_radius")),
            static_cast<uint32_t>(Integer(source.at("encoded_bytes"), 0, std::numeric_limits<int>::max()))};
        const auto sockets = source.value("sockets",Json::array());
        Array(sockets,0,64); socketNames_.resize(sockets.size()); sockets_.resize(sockets.size());
        for (size_t i=0;i<sockets.size();++i)
        {
            socketNames_[i]=sockets[i].at("id").get<std::string>();
            sockets_[i]={socketNames_[i].c_str(),static_cast<uint8_t>(Integer(sockets[i].at("bone"),0,static_cast<int>(joints.size())-1)),
                Transform(sockets[i])};
        }
        asset.sockets=sockets_.data(); asset.socket_count=static_cast<uint16_t>(sockets_.size());
        if (!dl_animation_validate(&asset, static_cast<int>(vertexBones_.size())))
            throw std::runtime_error("The N64 sampler rejected this cooked character asset");
        crossJointTriangles = Integer(source.at("cross_joint_triangles"), 0, 1000000);
        const auto& jointMeshes = source.at("joint_meshes");
        Array(jointMeshes, 0, joints.size());
        std::vector<bool> seen(joints.size());
        for (const auto& mesh : jointMeshes)
        {
            const int joint = Integer(mesh.at("joint"), 0, static_cast<int>(joints.size())-1);
            if (seen[joint]) throw std::runtime_error("Duplicate character joint preview mesh");
            seen[joint] = true;
            meshes.push_back({joint, mesh.at("uri").get<std::string>()});
        }
        sourceHash=source.value("source_sha256",std::string("legacy"));
        topologyHash=source.value("topology_sha256",std::string());
        if (crossJointTriangles && (topologyHash.size()!=64 ||
            topologyHash.find_first_not_of("0123456789abcdef")!=std::string::npos))
            throw std::runtime_error("Cooked character topology identity is missing. Save + Cook to refresh it.");
        if (source.contains("bind_mesh"))
        {
            const auto& mesh=source.at("bind_mesh");
            const auto& positions=mesh.at("vertices");
            const auto& normals=mesh.at("normals");
            const auto& uvs=mesh.at("uvs");
            const auto& triangles=mesh.at("indices");
            Array(positions,vertexBones_.size(),vertexBones_.size());
            Array(normals,positions.size(),positions.size()); Array(uvs,positions.size(),positions.size());
            Array(triangles,3,196605);
            if (triangles.size()%3) throw std::runtime_error("Cooked character has incomplete triangles");
            for (size_t i=0;i<positions.size();++i)
            {
                bindPositions.push_back(Vector(positions[i])); bindNormals.push_back(Vector(normals[i]));
                Array(uvs[i],2,2); bindUvs.push_back({Number(uvs[i][0]),Number(uvs[i][1])});
            }
            for (const auto& index : triangles)
                indices.push_back(static_cast<uint32_t>(Integer(index,0,static_cast<int>(positions.size())-1)));
        }
    }
    CharacterPreviewAsset(const CharacterPreviewAsset&) = delete;
    CharacterPreviewAsset& operator=(const CharacterPreviewAsset&) = delete;
};
