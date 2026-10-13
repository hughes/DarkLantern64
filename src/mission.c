#include "mission.h"
#include <float.h>
#include <math.h>
#include <string.h>

_Static_assert(sizeof(float)==4 && FLT_RADIX==2 && FLT_MANT_DIG==24 && FLT_MAX_EXP==128,
    "Mission files require IEEE-754 binary32 floats");

static uint16_t be16(const uint8_t *p){return (uint16_t)((uint16_t)p[0]<<8|p[1]);}
static uint32_t be32(const uint8_t *p){
    return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3];
}
static float f32(const uint8_t *p){uint32_t bits=be32(p);float value;memcpy(&value,&bits,4);return value;}
static DlMissionVec3 vector(const uint8_t *p){return (DlMissionVec3){f32(p),f32(p+4),f32(p+8)};}
static int finite_vector(DlMissionVec3 v){return isfinite(v.x)&&isfinite(v.y)&&isfinite(v.z);}
static int zeroes(const uint8_t *p,size_t n){while(n--)if(*p++)return 0;return 1;}
static int range(size_t total,size_t offset,size_t bytes){return offset<=total&&bytes<=total-offset;}

uint32_t dl_mission_crc32_update(uint32_t crc,const void *bytes,size_t size){
    static const uint32_t table[16]={
        0x00000000u,0x1db71064u,0x3b6e20c8u,0x26d930acu,
        0x76dc4190u,0x6b6b51f4u,0x4db26158u,0x5005713cu,
        0xedb88320u,0xf00f9344u,0xd6d6a3e8u,0xcb61b38cu,
        0x9b64c2b0u,0x86d3d2d4u,0xa00ae278u,0xbdbdf21cu};
    const uint8_t *p=bytes;
    while(size--){crc^=*p++;crc=(crc>>4)^table[crc&15];crc=(crc>>4)^table[crc&15];}
    return crc;
}
const char *dl_mission_status_name(DlMissionStatus status){
    static const char *const names[]={"ok","argument","truncated","header","checksum",
        "layout","reference","value","not-found"};
    return (unsigned)status<sizeof names/sizeof names[0]?names[status]:"unknown";
}
static void region_record(const uint8_t *p,DlMissionRegion *region){
    *region=(DlMissionRegion){.id=be32(p),.chunk_offset=be32(p+4),.chunk_bytes=be32(p+8),
        .decoded_bytes=be32(p+12),.chunk_crc32=be32(p+16),.first_dependency=be32(p+20),
        .dependency_count=be32(p+24),.minimum=vector(p+32),.maximum=vector(p+44)};
}
DlMissionStatus dl_mission_open(const void *directory,size_t directory_bytes,
    size_t actual_file_bytes,DlMission *mission){
    if(!mission)return DL_MISSION_ARGUMENT;
    memset(mission,0,sizeof *mission);
    if(!directory)return DL_MISSION_ARGUMENT;
    if(directory_bytes<DL_MISSION_HEADER_BYTES)return DL_MISSION_TRUNCATED;
    const uint8_t *data=directory;
    if(memcmp(data,"DLM1",4)||be16(data+4)!=DL_MISSION_VERSION||
        be16(data+6)!=DL_MISSION_HEADER_BYTES)return DL_MISSION_HEADER;
    if(directory_bytes>UINT32_MAX||actual_file_bytes>UINT32_MAX||
        be32(data+8)!=actual_file_bytes||be32(data+20)!=directory_bytes||
        directory_bytes>actual_file_bytes)return DL_MISSION_LAYOUT;
    uint32_t regions=be32(data+12),entities=be32(data+16),entry=be32(data+24);
    if(!regions||!entry||entry>regions)return DL_MISSION_REFERENCE;
    size_t remaining=directory_bytes-DL_MISSION_HEADER_BYTES;
    if(regions>remaining/DL_MISSION_REGION_BYTES)return DL_MISSION_TRUNCATED;
    size_t entity_offset=DL_MISSION_HEADER_BYTES+(size_t)regions*DL_MISSION_REGION_BYTES;
    remaining=directory_bytes-entity_offset;
    if(entities>remaining/DL_MISSION_ENTITY_BYTES)return DL_MISSION_TRUNCATED;
    size_t dependencies=entity_offset+(size_t)entities*DL_MISSION_ENTITY_BYTES;
    remaining=directory_bytes-dependencies;
    if(remaining%4)return DL_MISSION_LAYOUT;
    uint32_t dependency_count=(uint32_t)(remaining/4);
    static const uint8_t zero_checksum[4]={0};
    uint32_t crc=dl_mission_crc32_update(UINT32_MAX,data,28);
    crc=dl_mission_crc32_update(crc,zero_checksum,sizeof zero_checksum);
    crc=dl_mission_crc32_update(crc,data+32,directory_bytes-32)^UINT32_MAX;
    if(crc!=be32(data+28))return DL_MISSION_CHECKSUM;

    uint32_t next_dependency=0;
    size_t previous_end=directory_bytes;
    for(uint32_t i=0;i<regions;++i){
        const uint8_t *p=data+DL_MISSION_HEADER_BYTES+(size_t)i*DL_MISSION_REGION_BYTES;
        DlMissionRegion region;region_record(p,&region);
        if(region.id!=i+1)return DL_MISSION_REFERENCE;
        if(be32(p+28)||!zeroes(p+56,8)||region.chunk_bytes!=region.decoded_bytes||
            region.chunk_bytes<DL_MISSION_CHUNK_HEADER_BYTES+DL_MISSION_SECTION_BYTES||
            region.chunk_offset%16||region.chunk_offset<previous_end||
            !range(actual_file_bytes,region.chunk_offset,region.chunk_bytes))return DL_MISSION_LAYOUT;
        previous_end=(size_t)region.chunk_offset+region.chunk_bytes;
        if(!finite_vector(region.minimum)||!finite_vector(region.maximum)||
            region.minimum.x>=region.maximum.x||region.minimum.y>=region.maximum.y||
            region.minimum.z>=region.maximum.z)return DL_MISSION_VALUE;
        if(region.first_dependency!=next_dependency||
            region.dependency_count>dependency_count-next_dependency)return DL_MISSION_LAYOUT;
        for(uint32_t j=0;j<region.dependency_count;++j){
            uint32_t id=be32(data+dependencies+(size_t)(next_dependency+j)*4);
            if(!id||id>regions||id==region.id)return DL_MISSION_REFERENCE;
            for(uint32_t k=0;k<j;++k)
                if(id==be32(data+dependencies+(size_t)(next_dependency+k)*4))return DL_MISSION_REFERENCE;
        }
        next_dependency+=region.dependency_count;
    }
    if(next_dependency!=dependency_count)return DL_MISSION_LAYOUT;
    for(uint32_t i=0;i<entities;++i){
        const uint8_t *p=data+entity_offset+(size_t)i*DL_MISSION_ENTITY_BYTES;
        if(be32(p)!=i+1||!be32(p+4)||be32(p+4)>regions)return DL_MISSION_REFERENCE;
        if(be16(p+8)>DL_MISSION_ENTITY_OBJECTIVE)return DL_MISSION_VALUE;
        if(be16(p+10)||be32(p+12))return DL_MISSION_LAYOUT;
    }
    *mission=(DlMission){.directory=data,.directory_bytes=directory_bytes,
        .file_bytes=(uint32_t)actual_file_bytes,.region_count=regions,.entity_count=entities,
        .entry_region_id=entry,.dependency_count=dependency_count,.dependency_offset=(uint32_t)dependencies};
    memcpy(mission->identity,data+32,sizeof mission->identity);
    return DL_MISSION_OK;
}
DlMissionStatus dl_mission_region(const DlMission *mission,uint32_t id,DlMissionRegion *region){
    if(!mission||!mission->directory||!region)return DL_MISSION_ARGUMENT;
    if(!id||id>mission->region_count)return DL_MISSION_REFERENCE;
    size_t index=id-1;
    if(mission->directory_bytes<DL_MISSION_HEADER_BYTES||
        index>=(mission->directory_bytes-DL_MISSION_HEADER_BYTES)/DL_MISSION_REGION_BYTES)
        return DL_MISSION_LAYOUT;
    region_record(mission->directory+DL_MISSION_HEADER_BYTES+index*DL_MISSION_REGION_BYTES,region);
    return DL_MISSION_OK;
}
DlMissionStatus dl_mission_entity(const DlMission *mission,uint32_t id,DlMissionEntity *entity){
    if(!mission||!mission->directory||!entity)return DL_MISSION_ARGUMENT;
    if(!id||id>mission->entity_count)return DL_MISSION_REFERENCE;
    if(mission->directory_bytes<DL_MISSION_HEADER_BYTES||
        mission->region_count>(mission->directory_bytes-DL_MISSION_HEADER_BYTES)/DL_MISSION_REGION_BYTES)
        return DL_MISSION_LAYOUT;
    size_t offset=DL_MISSION_HEADER_BYTES+(size_t)mission->region_count*DL_MISSION_REGION_BYTES;
    size_t index=id-1;
    if(index>=(mission->directory_bytes-offset)/DL_MISSION_ENTITY_BYTES)return DL_MISSION_LAYOUT;
    const uint8_t *p=mission->directory+offset+index*DL_MISSION_ENTITY_BYTES;
    *entity=(DlMissionEntity){be32(p),be32(p+4),(DlMissionEntityKind)be16(p+8)};
    return DL_MISSION_OK;
}
DlMissionStatus dl_mission_dependency(const DlMission *mission,uint32_t region_id,
    uint32_t index,uint32_t *dependency_id){
    if(!dependency_id)return DL_MISSION_ARGUMENT;
    DlMissionRegion region;DlMissionStatus status=dl_mission_region(mission,region_id,&region);
    if(status!=DL_MISSION_OK)return status;
    if(index>=region.dependency_count)return DL_MISSION_REFERENCE;
    if(region.first_dependency>mission->dependency_count||
        index>=mission->dependency_count-region.first_dependency)return DL_MISSION_LAYOUT;
    size_t offset=mission->dependency_offset;
    if(offset>mission->directory_bytes||
        (size_t)region.first_dependency+index>=(mission->directory_bytes-offset)/4)return DL_MISSION_LAYOUT;
    *dependency_id=be32(mission->directory+offset+((size_t)region.first_dependency+index)*4);
    return DL_MISSION_OK;
}
DlMissionStatus dl_mission_chunk_open(const void *data,size_t bytes,DlMissionChunk *chunk){
    if(!chunk)return DL_MISSION_ARGUMENT;
    memset(chunk,0,sizeof *chunk);
    if(!data)return DL_MISSION_ARGUMENT;
    if(bytes<DL_MISSION_CHUNK_HEADER_BYTES)return DL_MISSION_TRUNCATED;
    const uint8_t *p=data;
    uint16_t version=be16(p+4);
    if(memcmp(p,"DLR1",4)||(version!=1&&version!=2&&version!=3&&version!=4)||
        be16(p+6)!=DL_MISSION_CHUNK_HEADER_BYTES)return DL_MISSION_HEADER;
    if(bytes>UINT32_MAX||be32(p+8)!=bytes||be32(p+28))return DL_MISSION_LAYOUT;
    uint32_t count=be32(p+12);
    if(!count||count>(version>=3?DL_MISSION_MAX_SECTIONS:3))return DL_MISSION_LAYOUT;
    size_t payload_min=DL_MISSION_CHUNK_HEADER_BYTES+(size_t)count*DL_MISSION_SECTION_BYTES;
    if(bytes<payload_min)return DL_MISSION_TRUNCATED;
    DlMissionChunk result={.data=p,.bytes=bytes,.origin=vector(p+16),.version=version,.section_count=count};
    if(!finite_vector(result.origin))return DL_MISSION_VALUE;
    unsigned previous_kind=0;
    for(uint32_t i=0;i<count;++i){
        const uint8_t *row=p+DL_MISSION_CHUNK_HEADER_BYTES+(size_t)i*DL_MISSION_SECTION_BYTES;
        unsigned kind=be16(row);
        if(kind<=previous_kind||kind>(version>=3?DL_MISSION_SECTION_FLOORS:DL_MISSION_SECTION_ENTITIES)||be16(row+2)||be32(row+20))
            return DL_MISSION_LAYOUT;
        previous_kind=kind;
        DlMissionSection *section=&result.sections[i];
        *section=(DlMissionSection){(DlMissionSectionKind)kind,be32(row+4),be32(row+8),be32(row+12),be32(row+16)};
        uint32_t stride=kind==DL_MISSION_SECTION_TRIANGLES?
            (version==4?DL_MISSION_TRIANGLE_V4_BYTES:version>=2?DL_MISSION_TRIANGLE_V2_BYTES:DL_MISSION_TRIANGLE_BYTES):
            kind==DL_MISSION_SECTION_COLLIDERS?DL_MISSION_COLLIDER_BYTES:
            kind==DL_MISSION_SECTION_FLOORS?DL_MISSION_FLOOR_BYTES:4;
        if(section->record_bytes!=stride||section->bytes%stride||section->count!=section->bytes/stride||
            section->offset%16||section->offset<payload_min||!range(bytes,section->offset,section->bytes))
            return DL_MISSION_LAYOUT;
        for(uint32_t j=0;j<i;++j){
            const DlMissionSection *other=&result.sections[j];
            if(section->bytes&&other->bytes&&section->offset<other->offset+other->bytes&&
                other->offset<section->offset+section->bytes)return DL_MISSION_LAYOUT;
        }
    }
    *chunk=result;
    return DL_MISSION_OK;
}
DlMissionStatus dl_mission_section(const DlMissionChunk *chunk,DlMissionSectionKind kind,DlMissionSection *section){
    if(!chunk||!chunk->data||!section)return DL_MISSION_ARGUMENT;
    if(chunk->section_count>DL_MISSION_MAX_SECTIONS)return DL_MISSION_LAYOUT;
    if(kind<DL_MISSION_SECTION_TRIANGLES||kind>DL_MISSION_SECTION_FLOORS)return DL_MISSION_ARGUMENT;
    for(uint32_t i=0;i<chunk->section_count;++i)if(chunk->sections[i].kind==kind){
        *section=chunk->sections[i];return DL_MISSION_OK;
    }
    return DL_MISSION_NOT_FOUND;
}
static DlMissionStatus record(const DlMissionChunk *chunk,DlMissionSectionKind kind,
    uint32_t index,const uint8_t **data){
    DlMissionSection section;DlMissionStatus status=dl_mission_section(chunk,kind,&section);
    if(status!=DL_MISSION_OK)return status;
    if(index>=section.count)return DL_MISSION_REFERENCE;
    uint32_t stride=kind==DL_MISSION_SECTION_TRIANGLES?
        (chunk->version==4?DL_MISSION_TRIANGLE_V4_BYTES:chunk->version>=2?DL_MISSION_TRIANGLE_V2_BYTES:DL_MISSION_TRIANGLE_BYTES):
        kind==DL_MISSION_SECTION_COLLIDERS?DL_MISSION_COLLIDER_BYTES:
        kind==DL_MISSION_SECTION_FLOORS?DL_MISSION_FLOOR_BYTES:4;
    if(section.record_bytes!=stride||!range(chunk->bytes,section.offset,section.bytes)||
        (size_t)index>=section.bytes/section.record_bytes)return DL_MISSION_LAYOUT;
    *data=chunk->data+section.offset+(size_t)index*section.record_bytes;
    return DL_MISSION_OK;
}
DlMissionStatus dl_mission_triangle(const DlMissionChunk *chunk,uint32_t index,DlMissionTriangle *triangle){
    if(!triangle)return DL_MISSION_ARGUMENT;
    const uint8_t *p;DlMissionStatus status=record(chunk,DL_MISSION_SECTION_TRIANGLES,index,&p);
    if(status!=DL_MISSION_OK)return status;
    DlMissionTriangle result={.positions={vector(p),vector(p+12),vector(p+24)},
        .material_id=be32(p+36),.surface_id=be32(p+40)};
    if(!finite_vector(result.positions[0])||!finite_vector(result.positions[1])||
        !finite_vector(result.positions[2]))return DL_MISSION_VALUE;
    if(!result.material_id||!result.surface_id)return DL_MISSION_REFERENCE;
    if(chunk->version>=2){
        result.attribute_flags=p[77];
        if(result.attribute_flags&~(DL_MISSION_TRIANGLE_UV|DL_MISSION_TRIANGLE_NORMAL)||p[78]||p[79])
            return DL_MISSION_LAYOUT;
        for(unsigned i=0;i<6;++i){result.uvs[i]=f32(p+44+i*4);if(!isfinite(result.uvs[i]))return DL_MISSION_VALUE;}
        for(unsigned i=0;i<9;++i){
            if(p[68+i]==128)return DL_MISSION_VALUE;
            result.normals[i]=(int8_t)(p[68+i]>127?(int)p[68+i]-256:p[68+i]);
        }
        if(result.attribute_flags&DL_MISSION_TRIANGLE_NORMAL)for(unsigned corner=0;corner<3;++corner)
            if(!result.normals[corner*3]&&!result.normals[corner*3+1]&&!result.normals[corner*3+2])
                return DL_MISSION_VALUE;
    }
    if(chunk->version==4){
        memcpy(result.baked_colors,p+80,sizeof result.baked_colors);
        for(unsigned state=0;state<2;++state)for(unsigned corner=0;corner<6;++corner)
            if(result.baked_colors[state][corner*4+3]!=255)return DL_MISSION_VALUE;
    }
    *triangle=result;return DL_MISSION_OK;
}
DlMissionStatus dl_mission_collider(const DlMissionChunk *chunk,const DlMission *mission,
    uint32_t index,DlMissionCollider *collider){
    if(!mission||!mission->directory||!collider)return DL_MISSION_ARGUMENT;
    const uint8_t *p;DlMissionStatus status=record(chunk,DL_MISSION_SECTION_COLLIDERS,index,&p);
    if(status!=DL_MISSION_OK)return status;
    DlMissionCollider result={.center=vector(p),.half_size=vector(p+12),.yaw=f32(p+24),.entity_id=be32(p+28)};
    if(!finite_vector(result.center)||!finite_vector(result.half_size)||!isfinite(result.yaw)||
        result.half_size.x<=0||result.half_size.y<=0||result.half_size.z<=0)return DL_MISSION_VALUE;
    if(result.entity_id){DlMissionEntity entity;status=dl_mission_entity(mission,result.entity_id,&entity);
        if(status!=DL_MISSION_OK)return status;}
    *collider=result;return DL_MISSION_OK;
}
DlMissionStatus dl_mission_entity_id(const DlMissionChunk *chunk,const DlMission *mission,
    uint32_t region_id,uint32_t index,uint32_t *entity_id){
    if(!mission||!mission->directory||!entity_id)return DL_MISSION_ARGUMENT;
    if(!region_id||region_id>mission->region_count)return DL_MISSION_REFERENCE;
    const uint8_t *p;DlMissionStatus status=record(chunk,DL_MISSION_SECTION_ENTITIES,index,&p);
    if(status!=DL_MISSION_OK)return status;
    uint32_t id=be32(p);DlMissionEntity entity;status=dl_mission_entity(mission,id,&entity);
    if(status!=DL_MISSION_OK)return status;
    if(entity.home_region_id!=region_id)return DL_MISSION_REFERENCE;
    *entity_id=id;return DL_MISSION_OK;
}

DlMissionStatus dl_mission_floor(const DlMissionChunk *chunk,uint32_t index,DlMissionFloorTriangle *floor){
    if(!floor)return DL_MISSION_ARGUMENT;
    if(!chunk||chunk->version<3)return DL_MISSION_ARGUMENT;
    const uint8_t *p;DlMissionStatus status=record(chunk,DL_MISSION_SECTION_FLOORS,index,&p);
    if(status!=DL_MISSION_OK)return status;
    DlMissionFloorTriangle result={.vertices={vector(p),vector(p+12),vector(p+24)}};
    for(unsigned i=0;i<3;++i)if(!finite_vector(result.vertices[i]))return DL_MISSION_VALUE;
    /* Double intermediates keep malformed finite f32 records from overflowing
     * during the footprint/grade check. Native world queries apply this same
     * walkable-floor contract after the chunk origin is added. */
    DlMissionVec3 a=result.vertices[0],b=result.vertices[1],c=result.vertices[2];
    double bx=(double)b.x-a.x,bz=(double)b.z-a.z,by=(double)b.y-a.y;
    double cx=(double)c.x-a.x,cz=(double)c.z-a.z,cy=(double)c.y-a.y;
    double determinant=bx*cz-bz*cx;
    if(!isfinite(determinant)||fabs(determinant)<=1e-8)return DL_MISSION_VALUE;
    double gx=(by*cz-bz*cy)/determinant,gz=(bx*cy-by*cx)/determinant;
    if(!isfinite(gx)||!isfinite(gz)||gx*gx+gz*gz>1.000001)return DL_MISSION_VALUE;
    *floor=result;return DL_MISSION_OK;
}
