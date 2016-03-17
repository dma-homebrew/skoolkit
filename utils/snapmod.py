#!/usr/bin/env python
import sys
import os
import argparse

SKOOLKIT_HOME = os.environ.get('SKOOLKIT_HOME')
if SKOOLKIT_HOME:
    if not os.path.isdir(SKOOLKIT_HOME):
        sys.stderr.write('SKOOLKIT_HOME={}: directory not found\n'.format(SKOOLKIT_HOME))
        sys.exit(1)
    sys.path.insert(0, SKOOLKIT_HOME)
else:
    try:
        import skoolkit
    except ImportError:
        sys.stderr.write('Error: SKOOLKIT_HOME is not set, and SkoolKit is not installed\n')
        sys.exit(1)

from skoolkit import get_word
from skoolkit.tap2sna import move, poke
from skoolkit.snapshot import get_snapshot, make_z80_ram_block, set_z80_registers, set_z80_state

def read_z80(z80file):
    with open(z80file, 'rb') as f:
        data = bytearray(f.read())
    if get_word(data, 6) > 0:
        header = data[:30]
    else:
        header_len = 32 + get_word(data, 30)
        header = data[:header_len]
    return list(header), get_snapshot(z80file)

def write_z80(header, snapshot, fname):
    if len(header) == 30:
        if header[12] & 32:
            ram = make_z80_ram_block(snapshot[16384:], 0)[3:] + [0, 237, 237, 0]
        else:
            ram = snapshot[16384:]
    else:
        ram = []
        for bank, data in ((5, snapshot[16384:32768]), (1, snapshot[32768:49152]), (2, snapshot[49152:])):
            ram += make_z80_ram_block(data, bank + 3)
    with open(fname, 'wb') as f:
        f.write(bytearray(header + ram))

def run(infile, options, outfile):
    header, snapshot = read_z80(infile)
    for spec in options.moves:
        move(snapshot, spec)
    for spec in options.pokes:
        poke(snapshot, spec)
    set_z80_registers(header, *options.reg)
    set_z80_state(header, *options.state)
    write_z80(header, snapshot, outfile)

###############################################################################
# Begin
###############################################################################
parser = argparse.ArgumentParser(
    usage='snapmod.py [options] in.z80 [out.z80]',
    description="Modify a 48K Z80 snapshot.",
    add_help=False
)
parser.add_argument('infile', help=argparse.SUPPRESS, nargs='?')
parser.add_argument('outfile', help=argparse.SUPPRESS, nargs='?')
group = parser.add_argument_group('Options')
group.add_argument('-f', dest='force', action='store_true',
                   help="Overwrite an existing snapshot")
group.add_argument('-m', dest='moves', metavar='src,size,dest', action='append', default=[],
                   help='Move a block of bytes of the given size from src to dest. This option may be used multiple times.')
group.add_argument('-p', dest='pokes', metavar='a[-b[-c]],[^+]v', action='append', default=[],
                   help="POKE N,v for N in {a, a+c, a+2c..., b}. "
                        "Prefix 'v' with '^' to perform an XOR operation, or '+' to perform an ADD operation. "
                        "This option may be used multiple times.")
group.add_argument('-r', dest='reg', metavar='name=value', action='append', default=[],
                   help="Set the value of a register. This option may be used multiple times.")
group.add_argument('-s', dest='state', metavar='name=value', action='append', default=[],
                   help="Set a hardware state attribute (border, iff, im). This option may be used multiple times.")
namespace, unknown_args = parser.parse_known_args()
infile = namespace.infile
outfile = namespace.outfile
if unknown_args or infile is None:
    parser.exit(2, parser.format_help())
if not infile.lower().endswith('.z80'):
    sys.stderr.write('Error: unrecognised input snapshot type\n')
    sys.exit(1)

if outfile is None:
    outfile = infile
if namespace.force or not os.path.isfile(outfile):
    run(infile, namespace, outfile)
else:
    print('{}: file already exists; use -f to overwrite'.format(outfile))