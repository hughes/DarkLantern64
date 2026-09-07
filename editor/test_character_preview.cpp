#include "character_preview.h"
#include <cmath>
#include <iostream>
#include <stdexcept>

using Json=nlohmann::json;
static void Check(bool condition,const char* label)
{
    if (!condition) throw std::runtime_error(label);
}
static Json Fixture()
{
    return Json::parse(R"({
      "schema_version":1,"id":"test",
      "bones":[{"name":"head","parent":-1,
        "rest":{"translation":[0,0,0],"rotation":[0,0,0,1]},
        "inverse_bind":{"translation":[0,0,0],"rotation":[0,0,0,1]}}],
      "vertex_bones":[0,0,0],
      "clips":[{"id":"turn","duration":1,"stride_length":0,"translation_scale":0.001,
        "sample_count":2,"loop":false,
        "tracks":[{"bone":0,"channel":"rotation","sample_count":2,
          "values":[0,0,0,32767,0,23170,0,23170]}],
        "events":[{"phase":32768,"kind":"foot_left"}]}],
      "head_bone":0,"bounds_center":[0,0,0],"bounds_radius":2,"encoded_bytes":16,
      "cross_joint_triangles":0,"joint_meshes":[{"joint":0,"uri":"meshes/test/head.gltf"}]
    })");
}
static void Reject(Json value,const char* label)
{
    try { CharacterPreviewAsset ignored(value); }
    catch (const std::exception&) { return; }
    throw std::runtime_error(label);
}
int main()
{
    try
    {
        CharacterPreviewAsset data(Fixture());
        DlAnimMatrix skin[DL_ANIMATION_MAX_BONES];
        Check(dl_animation_pose(&data.asset,0,.5f,-1,0,0,0,0,skin),"Cooked sampler pose rejected");
        const DlVec3 middle=dl_animation_point(skin,{0,0,1});
        Check(std::abs(middle.x-.70710678f)<.0001f && std::abs(middle.z-.70710678f)<.0001f,
              "Cooked integer rotation differs from the expected halfway pose");
        Check(dl_animation_pose(&data.asset,-1,0,-1,0,0,.78539816f,0,skin),"Attention pose rejected");
        const DlVec3 attention=dl_animation_point(skin,{0,0,1});
        Check(std::abs(attention.x-middle.x)<.0001f,"Attention overlay differs from equivalent authored rotation");
        Check(dl_animation_event_count(&data.asset.clips[0],0,.75f,DL_ANIM_FOOT_LEFT)==1,"Cooked foot marker missing");
        auto bad=Fixture(); bad["clips"][0]["tracks"][0]["values"].erase(0);
        Reject(bad,"Truncated key buffer accepted");
        bad=Fixture(); bad["bones"][0]["parent"]=0;
        Reject(bad,"Cyclic skeleton accepted");
        bad=Fixture(); bad["vertex_bones"][0]=256;
        Reject(bad,"Wrapped vertex joint accepted");
        bad=Fixture(); bad["clips"][0]["tracks"][0]["values"][0]=40000;
        Reject(bad,"Wrapped int16 key accepted");
        bad=Fixture(); bad["joint_meshes"].push_back(bad["joint_meshes"][0]);
        Reject(bad,"Duplicate joint geometry accepted");
        bad=Fixture(); bad["clips"][0]["tracks"].push_back(bad["clips"][0]["tracks"][0]);
        Reject(bad,"Duplicate pose channel accepted");
        std::cout << "Character preview adapter: quantized pose, attention, events and six malformed asset checks passed\n";
        return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
