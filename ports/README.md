# Overlay ports

This folder holds overlay ports for recipes the pinned vcpkg baseline can no longer build. The baseline stays at `6ecbbbdf31cba47aafa7cf6189b1e73e10ac61f8`.

`gmp/` contains the [GMP port files at that baseline](https://github.com/microsoft/vcpkg/tree/6ecbbbdf31cba47aafa7cf6189b1e73e10ac61f8/ports/gmp). The publisher rotated `autoconf2.71-2.71-3-any.pkg.tar.zst` out of every configured mirror. Only the autoconf download URL and SHA-512 change, using the values from [vcpkg fix 37bb045f3c7a747d3e5d1c13b6fe6a0aec4b5d00](https://github.com/microsoft/vcpkg/commit/37bb045f3c7a747d3e5d1c13b6fe6a0aec4b5d00). They select MSYS2 autoconf 2.71 package build 4. GMP stays at 6.3.0, port revision 2.

The port files are vcpkg's under the MIT licence. Its copyright notice and licence text are kept in [LICENSE-vcpkg.txt](LICENSE-vcpkg.txt). The built libraries retain their own licences.
