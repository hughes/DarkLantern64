#include "character_preview.h"
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

int main(int argc,char** argv)
{
    try
    {
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
