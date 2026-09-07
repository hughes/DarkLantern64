#include "character_preview.h"
#include "character_preview_mesh.h"
#include <assets/AssetManager.h>
#include <util/GltfLoader.h>
#include <tools/cpp/runfiles/runfiles.h>
#include <array>
#include <cmath>
#include <iostream>
#include <stdexcept>

static void Check(bool condition,const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

static void CheckTangentFallback()
{
    MeshData mesh;
    for (const glm::vec3 position : std::array<glm::vec3,5>{{{0,0,0},{1,0,0},{0,1,0},{-1,0,0},{0,-1,0}}})
    {
        Vertex vertex;
        vertex.position=position; vertex.normal={0,0,.99f};
        mesh.vertices.push_back(vertex);
    }
    mesh.vertices[1].texcoord=mesh.vertices[3].texcoord={1,0};
    mesh.vertices[2].texcoord=mesh.vertices[4].texcoord={0,1};
    mesh.indices={0,1,2,0,3,4};
    auto reference=mesh;
    ComputeTangents(reference);
    Check(PrepareCharacterPreviewTangents(mesh)==1,"Opposing UV contributions must repair only their shared vertex");
    for (size_t i=1;i<mesh.vertices.size();++i)
        Check(mesh.vertices[i].tangent==reference.vertices[i].tangent && mesh.vertices[i].bitangent==reference.vertices[i].bitangent,
              "Valid authored tangent frames changed");
    for (auto& vertex : mesh.vertices) vertex.texcoord={.5f,.5f};
    const auto authored=mesh;
    Check(PrepareCharacterPreviewTangents(mesh)==5,"Collapsed UV triangles need finite tangent frames at every vertex");
    for (size_t i=0;i<mesh.vertices.size();++i)
    {
        const auto& vertex=mesh.vertices[i];
        const auto normal=glm::normalize(vertex.normal);
        Check(vertex.position==authored.vertices[i].position && vertex.normal==authored.vertices[i].normal &&
              vertex.texcoord==authored.vertices[i].texcoord,"Tangent repair changed authored geometry or UVs");
        Check(std::isfinite(glm::length(vertex.tangent)) && std::isfinite(glm::length(vertex.bitangent)) &&
              std::abs(glm::length(vertex.tangent)-1)<1e-6f && std::abs(glm::length(vertex.bitangent)-1)<1e-6f &&
              std::abs(glm::dot(normal,vertex.tangent))<1e-6f && std::abs(glm::dot(normal,vertex.bitangent))<1e-6f &&
              std::abs(glm::dot(vertex.tangent,vertex.bitangent))<1e-6f,"Fallback frame is not finite and orthonormal");
    }
    const auto vertices=mesh.vertices;
    AssetMgr assets(std::filesystem::path("."));
    const auto handle=assets.RegisterImportedMesh("gen://character-tangent-regression",std::move(mesh));
    auto posed=vertices;
    for (auto& vertex : posed) vertex.position.y+=.25f;
    Check(assets.UpdateMeshVertices(handle,posed),"Real engine rejected repaired dynamic character vertices");
    Check(assets.VertexArena().size()==vertices.size(),"Updating repaired vertices grew the engine arena");
    std::cout << "Character tangent fallback: collapsed/cancelling UVs preserve authored data and pass real dynamic upload\n";
}

int main(int argc,char** argv)
{
    try
    {
        CheckTangentFallback();
        Check(argc==2,"Supply the asymmetric glTF UV fixture");
        std::string error;
        std::unique_ptr<bazel::tools::cpp::runfiles::Runfiles> files(
            bazel::tools::cpp::runfiles::Runfiles::CreateForTest(&error));
        Check(files!=nullptr,"Bazel could not locate the material test runfiles");
        const auto fixture=files->Rlocation(std::string("_main/")+argv[1]);
        Check(std::filesystem::is_regular_file(fixture),"Asymmetric UV fixture is missing from runfiles");
        const auto imported=LoadGltfMesh(fixture);
        Check(imported.has_value() && imported->vertices.size()==3,"LightEngine could not import the UV fixture");
        const std::array<DlVec2,3> cooked{{{.125f,.125f},{.625f,.375f},{.875f,.875f}}};
        // An asymmetric 4x4 atlas with distinct numbered colors in each cell,
        // laid out from its top row. LightEngine flips image rows on upload.
        const std::array<int,3> expectedPaletteCells{{0,6,15}};
        for (size_t i=0;i<cooked.size();++i)
        {
            const auto dynamic=CharacterPreviewAsset::ToLightEngineUv(cooked[i]);
            const auto reference=imported->vertices[i].texcoord;
            Check(std::abs(dynamic.u-reference.x)<.00001f && std::abs(dynamic.v-reference.y)<.00001f,
                  "Dynamic character UVs differ from LightEngine's real static glTF importer");
            const int x=static_cast<int>(dynamic.u*4), uploadedRow=static_cast<int>(dynamic.v*4);
            const int paletteCell=(3-uploadedRow)*4+x;
            Check(paletteCell==expectedPaletteCells[i],"Dynamic mesh samples the vertically opposite atlas material");
        }
        std::cout << "Character material preview: three asymmetric atlas samples match the real LightEngine glTF path\n";
        return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
