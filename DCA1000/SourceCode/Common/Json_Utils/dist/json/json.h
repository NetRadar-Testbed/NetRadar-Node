/*
 * NetRadar shim (Apache-2.0): TI's DCA1000 CLI sources include
 * "../Common/Json_Utils/dist/json/json.h", which in TI's distribution is the
 * amalgamated jsoncpp header. NetRadar builds against the system jsoncpp
 * instead (Ubuntu/JetPack: `sudo apt install libjsoncpp-dev`), so this file
 * simply forwards to it. The makefile adds `pkg-config --cflags jsoncpp`.
 */
#ifndef NETRADAR_JSONCPP_SHIM_JSON_H
#define NETRADAR_JSONCPP_SHIM_JSON_H
#include <json/json.h>
#endif
