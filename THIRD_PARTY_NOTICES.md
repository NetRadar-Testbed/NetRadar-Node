# Third-party notices

NetRadar-Node's own code is licensed under Apache-2.0 (see `LICENSE`). This
repository also contains, or installs, the third-party components below. Each
keeps its own licence. NetRadar is not affiliated with or endorsed by Texas
Instruments or FTDI.

## Bundled in this repository

### 1. Texas Instruments — mmWaveLink, FTDI port layer, firmware image, DCA1000 CLI

Files:

| Path | Origin |
| :--- | :--- |
| `setup_radar/ti/control/mmwavelink/` | TI mmWaveLink library (© 2014 Texas Instruments) |
| `setup_radar/ti/example/mmwavelink_example_nonos/` | TI mmWaveLink example, **adapted by the NetRadar authors** to configure the AWR2243 over FTDI (may differ from TI's distribution; `mmw_config.c` and `mmw_example_nonos.c` modified in 2026) |
| `setup_radar/ti/example/platform/mmwlstudio/` | TI platform headers |
| `setup_radar/FTDILib/SourceCode/` | TI mmWaveLink FTDI port layer |
| `setup_radar/firmware/xwr22xx_metaImage.h` | TI radar firmware image |
| `DCA1000/SourceCode/` (C/C++ sources) | TI DCA1000EVM CLI (© 2019 Texas Instruments) |
| `DCA1000/SourceCode/makefile` | TI, **modified by the NetRadar authors** to build against the system jsoncpp |
| `DCA1000/SourceCode/Release/DCAconfig.json` | TI configuration, **modified by the NetRadar authors** (paths) |

Not from TI: `DCA1000/SourceCode/Common/Json_Utils/dist/json/*.h` are small
NetRadar shims (Apache-2.0) that forward to the system jsoncpp.

Licence (BSD-3-Clause, as stated in each file's header):

```
(C) Copyright 2014, 2019 Texas Instruments Incorporated - www.ti.com

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

  Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

  Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in the
  documentation and/or other materials provided with the
  distribution.

  Neither the name of Texas Instruments Incorporated nor the names of
  its contributors may be used to endorse or promote products derived
  from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

TI's user and developer guides for the DCA1000EVM CLI are not redistributed
here; get them from TI (they ship with mmWave Studio).

### 2. FTDI — D2XX driver for Linux ARMv8 (version 1.4.34)

Files: `third_party/ftdi-d2xx/linux-arm-v8/` — `libftd2xx.so.1.4.34`,
`ftd2xx.h`, `WinTypes.h`, `release-notes.txt`, `README.pdf`, redistributed
**unmodified**. SHA-256 of the library:
`95c2f4b2d57c29bd7dd846df5c247347cc74ef92c36de72ac9def6707d741411`
(`install.sh` verifies it before installing).

Licence terms (from `ftd2xx.h`, © 2001-2021 Future Technology Devices
International Limited):

```
THIS SOFTWARE IS PROVIDED BY FUTURE TECHNOLOGY DEVICES INTERNATIONAL LIMITED "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
FUTURE TECHNOLOGY DEVICES INTERNATIONAL LIMITED BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
OF SUBSTITUTE GOODS OR SERVICES LOSS OF USE, DATA, OR PROFITS OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
FTDI DRIVERS MAY BE USED ONLY IN CONJUNCTION WITH PRODUCTS BASED ON FTDI PARTS.
FTDI DRIVERS MAY BE DISTRIBUTED IN ANY FORM AS LONG AS LICENSE INFORMATION IS NOT MODIFIED.
```

The AWR2243BOOST board's USB interface is an FTDI device, so NetRadar's use
complies with the "FTDI parts" condition. The driver is **not** covered by
Apache-2.0.

## Installed by `install.sh` (not bundled)

| Component | Source | Licence |
| :--- | :--- | :--- |
| JETGPIO (GPIO trigger library) | github.com/Rubberazer/JETGPIO, pinned commit `c69401c` | MIT |
| jsoncpp | Ubuntu package `libjsoncpp-dev` | MIT / public domain |
| Other system libraries (FFTW, OpenCV, Eigen, fmt, …) | Ubuntu packages | their respective licences |
| Python packages (`requirements.txt`, PyTorch) | PyPI | their respective licences |

## Loaded at runtime

The node visualizer page loads the Socket.IO client (MIT) from
`cdn.socket.io` in your browser.
