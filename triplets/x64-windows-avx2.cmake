set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE dynamic)
set(VCPKG_C_FLAGS "/arch:AVX2")
set(VCPKG_CXX_FLAGS "/arch:AVX2")
# /arch:AVX2 is MSVC syntax. With no Fortran compiler on the runner,
# vcpkg builds reference LAPACK with MinGW. vcpkg supplies its Fortran
# flags; this triplet does not set them.
if(PORT STREQUAL "lapack-reference")
    set(VCPKG_C_FLAGS "")
    set(VCPKG_CXX_FLAGS "")
endif()
set(VCPKG_BUILD_TYPE release)
