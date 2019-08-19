# Copyright 2019 Richard Dymond (rjdymond@gmail.com)
#
# This file is part of SkoolKit.
#
# SkoolKit is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# SkoolKit is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# SkoolKit. If not, see <http://www.gnu.org/licenses/>.

from skoolkit import get_class
from skoolkit.config import get_config

SK_CONFIG = None

def get_component_class(component):
    global SK_CONFIG
    if SK_CONFIG is None:
        SK_CONFIG = get_config('skoolkit')
    return get_class(SK_CONFIG[component])

def get_disassembler(snapshot, defb_size, defb_mod, zfill, defm_width, asm_hex, asm_lower):
    cls = get_component_class('Disassembler')
    return cls(snapshot, defb_size, defb_mod, zfill, defm_width, asm_hex, asm_lower)