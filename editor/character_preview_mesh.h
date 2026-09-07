#pragma once

#include <cmath>
#include <stdexcept>
#include <core/Types.h>
#include <util/GeometryUtil.h>

// Albedo-only characters may legitimately collapse cap UVs to a line or point.
// LightEngine's tangent generator also has undefined results when neighboring
// triangle contributions cancel. Repair only those undefined frames before the
// mesh enters the engine arena; authored positions, normals and UVs stay exact.
inline size_t PrepareCharacterPreviewTangents(MeshData& mesh)
{
    ComputeTangents(mesh);
    const auto usable = [](const glm::vec3& value)
    {
        const float squared = glm::dot(value, value);
        return std::isfinite(value.x) && std::isfinite(value.y) && std::isfinite(value.z) &&
               std::isfinite(squared) && squared > 1e-12f;
    };
    size_t repaired = 0;
    for (auto& vertex : mesh.vertices)
    {
        if (usable(vertex.tangent) && usable(vertex.bitangent)) continue;
        if (!usable(vertex.normal))
            throw std::runtime_error("Character tangent fallback needs a finite, nonzero authored normal");
        const glm::vec3 normal = glm::normalize(vertex.normal);
        const glm::vec3 absolute = glm::abs(normal);
        const glm::vec3 axis = absolute.x <= absolute.y && absolute.x <= absolute.z ? glm::vec3(1,0,0) :
                              absolute.y <= absolute.z ? glm::vec3(0,1,0) : glm::vec3(0,0,1);
        vertex.tangent = glm::normalize(glm::cross(axis, normal));
        vertex.bitangent = glm::normalize(glm::cross(normal, vertex.tangent));
        ++repaired;
    }
    return repaired;
}
