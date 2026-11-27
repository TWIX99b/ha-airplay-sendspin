"""Let shairport-sync report a real model and manufacturer (build-time patch).

shairport-sync always reports model=ShairportSync and no manufacturer. iOS
uses these to pick the icon in the AirPlay menu (Sonos reports e.g.
model=One, manufacturer=Sonos). After this patch, each instance reads
SPS_MODEL and SPS_MANUFACTURER from its environment; unset = unchanged.

    python3 sps_identity.py /path/to/shairport-sync-source
"""

import pathlib
import sys

src = pathlib.Path(sys.argv[1])

MANUFACTURER_TXT = """
    if (getenv("SPS_MANUFACTURER") && *getenv("SPS_MANUFACTURER")) {
      static char manufacturerString[128];
      secondary_txt_records[entry_number++] = bnprintf(
          manufacturerString, sizeof(manufacturerString), "manufacturer=%s",
          getenv("SPS_MANUFACTURER"));
    }"""

PATCHES = {
    "shairport.c": [
        (
            'config.model = strdup("ShairportSync");',
            'config.model = strdup((getenv("SPS_MODEL") && *getenv("SPS_MODEL"))'
            ' ? getenv("SPS_MODEL") : "ShairportSync");',
        ),
    ],
    "bonjour_strings.c": [
        ('#include <inttypes.h>', '#include <inttypes.h>\n#include <stdlib.h>'),
        (
            'bnprintf(modelString, sizeof(modelString), "model=%s", config.model);',
            'bnprintf(modelString, sizeof(modelString), "model=%s", config.model);'
            + MANUFACTURER_TXT,
        ),
    ],
    "rtsp.c": [
        (
            'if (add_pstring_to_malloc(bnprintf(localString, sizeof(localString), '
            '"model=%s", config.model),\n'
            '                            &qualifier_response_data, '
            '&qualifier_response_data_length) == 0)\n'
            '    debug(1, "Problem");',
            'if (add_pstring_to_malloc(bnprintf(localString, sizeof(localString), '
            '"model=%s", config.model),\n'
            '                            &qualifier_response_data, '
            '&qualifier_response_data_length) == 0)\n'
            '    debug(1, "Problem");\n'
            '  if (getenv("SPS_MANUFACTURER") && *getenv("SPS_MANUFACTURER"))\n'
            '    if (add_pstring_to_malloc(bnprintf(localString, sizeof(localString), '
            '"manufacturer=%s", getenv("SPS_MANUFACTURER")),\n'
            '                              &qualifier_response_data, '
            '&qualifier_response_data_length) == 0)\n'
            '      debug(1, "Problem");',
        ),
        (
            'plist_dict_set_item(response_plist, "model", plist_new_string(config.model));',
            'plist_dict_set_item(response_plist, "model", plist_new_string(config.model));\n'
            '    if (getenv("SPS_MANUFACTURER") && *getenv("SPS_MANUFACTURER"))\n'
            '      plist_dict_set_item(response_plist, "manufacturer",\n'
            '                          plist_new_string(getenv("SPS_MANUFACTURER")));',
        ),
    ],
}

for name, edits in PATCHES.items():
    path = src / name
    text = path.read_text()
    for old, new in edits:
        if text.count(old) != 1:
            sys.exit(f"sps_identity: '{old[:60]}…' found {text.count(old)}× in {name}")
        text = text.replace(old, new)
    path.write_text(text)
print("sps_identity: patched model/manufacturer")
