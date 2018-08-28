# Copyright 2009-2018 Richard Dymond (rjdymond@gmail.com)
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

import sys
import os

from skoolkit import (SkoolKitError, open_file, read_bin_file, warn, write_line,
                      wrap, parse_int, get_address_format, format_template)
from skoolkit.ctlparser import CtlParser
from skoolkit.disassembler import Disassembler
from skoolkit.skoolasm import UDGTABLE_MARKER
from skoolkit.skoolctl import (AD_IGNOREUA, AD_LABEL, AD_ORG, AD_START, TITLE,
                               DESCRIPTION, REGISTERS, MID_BLOCK, INSTRUCTION, END)
from skoolkit.skoolmacro import ClosingBracketError, parse_brackets
from skoolkit.skoolparser import (get_address, TABLE_MARKER, TABLE_END_MARKER,
                                  LIST_MARKER, LIST_END_MARKER)

OP_WIDTH = 13
MIN_COMMENT_WIDTH = 10
MIN_INSTRUCTION_COMMENT_WIDTH = 10

# The maximum number of distinct bytes that can be in a data block (as a
# fraction of the block length)
UNIQUE_BYTES_MAX = 0.3

# The minimum allowed length of a text block
MIN_LENGTH = 3
# The minimum number of distinct characters that must be in a text block (as a
# fraction of the block length)
UNIQUE_CHARS_MIN = 0.25
# The maximum number of punctuation characters that can be in a text block (as
# a fraction of the block length)
PUNC_CHARS_MAX = 0.2
# The characters allowed in a text block
CHARS = ' ,.abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
# The punctuation characters allowed in a text block
PUNC_CHARS = ',.'
# If two text blocks are separated by no more than this number of bytes, they
# will be joined
TEXT_GAP_MAX = 8

class CodeMapError(SkoolKitError):
    pass

def _get_code_blocks(snapshot, start, end, fname):
    if os.path.isdir(fname):
        raise SkoolKitError('{0} is a directory'.format(fname))
    try:
        size = os.path.getsize(fname)
    except OSError as e:
        if e.errno == 2:
            raise SkoolKitError('{0}: file not found'.format(fname))
        raise SkoolKitError('Failed to get size of {}: {}'.format(fname, e.strerror))

    if size == 8192:
        # Assume this is a Z80 map file
        sys.stderr.write('Reading {0}'.format(fname))
        sys.stderr.flush()
        addresses = []
        data = read_bin_file(fname)
        address = start & 65528
        for b in data[start // 8:end // 8 + 1]:
            for i in range(8):
                if b & 1 and start <= address < end:
                    addresses.append(address)
                b >>= 1
                address += 1
    elif size == 65536:
        # Assume this is a SpecEmu map file
        sys.stderr.write('Reading {}'.format(fname))
        sys.stderr.flush()
        addresses = []
        data = read_bin_file(fname)
        for address in range(start, end):
            if data[address] & 1:
                addresses.append(address)
    else:
        sys.stderr.write('Reading {0}: '.format(fname))
        sys.stderr.flush()
        with open_file(fname) as f:
            addresses = _get_addresses(f, fname, size, start, end)
    sys.stderr.write('\n')

    code_blocks = []
    disassembler = Disassembler(snapshot)
    for address in addresses:
        size = disassembler.disassemble(address, address + 1)[0].size()
        if code_blocks and address <= sum(code_blocks[-1]):
            if address == sum(code_blocks[-1]):
                code_blocks[-1][1] += size
        else:
            code_blocks.append([address, size])

    return code_blocks

def _get_addresses(f, fname, size, start, end):
    addresses = set()
    base = 16
    i = 1
    rewind = True
    ignore_prefixes = ()

    s_line = ''
    while 1:
        line = f.readline()
        if not line:
            break
        i += 1
        s_line = line.strip()
        if s_line:
            break

    if s_line.startswith('0x'):
        # Fuse profile
        address_f = lambda s_line: s_line[2:6]
    elif s_line.startswith('PC = '):
        # Spud log
        address_f = lambda s_line: s_line[5:9]
    elif s_line.startswith('PC:'):
        # SpecEmu log
        address_f = lambda s_line: s_line[:4]
        ignore_prefixes = ('PC:', 'IX:', 'HL:', 'DE:', 'BC:', 'AF:')
        rewind = False
    elif s_line.endswith('decimal'):
        # Zero log
        if s_line.endswith('in decimal'):
            base = 10
        address_f = lambda s_line: s_line[:s_line.find('\t')]
        rewind = False
    else:
        raise CodeMapError('{0}: Unrecognised format'.format(fname))

    if rewind:
        f.seek(0)
        i = 1

    while 1:
        line = f.readline()
        if not line:
            break
        progress_msg = '{0}%'.format((100 * f.tell()) // size)
        sys.stderr.write(progress_msg + chr(8) * len(progress_msg))
        sys.stderr.flush()
        s_line = line.strip()
        if s_line:
            address_str = address_f(s_line)
            address = None
            if address_str:
                try:
                    address = int(address_str, base)
                except ValueError:
                    if not (ignore_prefixes and s_line.startswith(ignore_prefixes)):
                        raise CodeMapError('{0}, line {1}: Cannot parse address: {2}'.format(fname, i, s_line))
                if address is not None:
                    if address < 0 or address > 65535:
                        raise CodeMapError('{0}, line {1}: Address out of range: {2}'.format(fname, i, s_line))
                    if start <= address < end:
                        addresses.add(address)
        i += 1

    return sorted(addresses)

def _is_terminal_instruction(instruction):
    data = instruction.bytes
    if data[0] in (195, 201, 233):
        # JP nn / RET / JP (HL)
        return True
    if len(data) == 2:
        if data[0] == 237 and data[1] in (69, 77, 85, 93, 101, 109, 117, 125):
            # RETN/RETI
            return True
        if data[0] in (221, 253) and data[1] == 233:
            # JP (IX)/JP (IY)
            return True
        if data[0] == 24 and data[1] > 0:
            # JR d (d != 0)
            return True
    return False

def _find_terminal_instruction(disassembler, ctls, start, end=65536, ctl=None):
    address = start
    while address < end:
        instruction = disassembler.disassemble(address, address + 1)[0]
        address = instruction.address + instruction.size()
        if ctl is None:
            for a in range(instruction.address, address):
                if a in ctls:
                    next_ctl = ctls[a]
                    del ctls[a]
            if ctls.get(address) == 'c':
                break
        if _is_terminal_instruction(instruction):
            if address < 65536 and address not in ctls:
                ctls[address] = ctl or next_ctl
            break
    return address

def _generate_ctls_with_code_map(snapshot, start, end, code_map):
    # (1) Use the code map to create an initial set of 'c' ctls, and mark all
    #     unexecuted blocks as 'U' (unknown)
    # (2) Where a 'c' block doesn't end with a RET/JP/JR, extend it up to the
    #     next RET/JP/JR in the following 'U' blocks, or up to the next 'c'
    #     block
    # (3) Mark entry points in 'U' blocks that are CALLed or JPed to from 'c'
    #     blocks with 'c'
    # (4) Split 'c' blocks on RET/JP/JR
    # (5) Scan the disassembly for pairs of adjacent blocks where the start
    #     address of the second block is JRed or JPed to from the first block,
    #     and join such pairs
    # (6) Examine the remaining 'U' blocks for text
    # (7) Mark data blocks of all zeroes with 's'

    # (1) Mark all executed blocks as 'c' and unexecuted blocks as 'U'
    # (unknown)
    ctls = {start: 'U', end: 'i'}
    for address, length in _get_code_blocks(snapshot, start, end, code_map):
        ctls[address] = 'c'
        if address + length < end:
            ctls[address + length] = 'U'

    # (2) Where a 'c' block doesn't end with a RET/JP/JR, extend it up to the
    # next RET/JP/JR in the following 'U' blocks, or up to the next 'c' block
    disassembler = Disassembler(snapshot)
    while 1:
        done = True
        for ctl, b_start, b_end in _get_blocks(ctls):
            if ctl == 'c':
                if _is_terminal_instruction(disassembler.disassemble(b_start, b_end)[-1]):
                    continue
                if _find_terminal_instruction(disassembler, ctls, b_end, end) < end:
                    done = False
                    break
        if done:
            break

    # (3) Mark entry points in 'U' blocks that are CALLed or JPed to from 'c'
    # blocks with 'c'
    ctl_parser = CtlParser(ctls)
    disassembly = Disassembly(snapshot, ctl_parser)
    while 1:
        disassembly.build(True)
        done = True
        for entry in disassembly.entries:
            if entry.ctl == 'U':
                for instruction in entry.instructions:
                    for referrer in instruction.referrers:
                        if ctls[referrer.address] == 'c':
                            ctls[instruction.address] = 'c'
                            if entry.next:
                                e_end = entry.next.address
                            else:
                                e_end = 65536
                            _find_terminal_instruction(disassembler, ctls, instruction.address, e_end, entry.ctl)
                            disassembly.remove_entry(entry.address)
                            done = False
                            break
                    if not done:
                        break
                if not done:
                    break
        if done:
            break

    # (4) Split 'c' blocks on RET/JP/JR
    for ctl, b_address, b_end in _get_blocks(ctls):
        if ctl == 'c':
            next_address = _find_terminal_instruction(disassembler, ctls, b_address, b_end, 'c')
            if next_address < b_end:
                disassembly.remove_entry(b_address)
                while next_address < b_end:
                    next_address = _find_terminal_instruction(disassembler, ctls, next_address, b_end, 'c')

    # (5) Scan the disassembly for pairs of adjacent blocks where the start
    # address of the second block is JRed or JPed to from the first block, and
    # join such pairs
    while 1:
        disassembly.build()
        done = True
        for entry in disassembly.entries[:-1]:
            if entry.ctl == 'c':
                for instruction in entry.instructions:
                    operation = instruction.operation
                    if operation[:2] in ('JR', 'JP') and operation[-5:] == str(entry.next.address):
                        del ctls[entry.next.address]
                        disassembly.remove_entry(entry.address)
                        disassembly.remove_entry(entry.next.address)
                        done = False
                        break
        if done:
            break

    # (6) Examine the 'U' blocks for text/data
    for ctl, b_start, b_end in _get_blocks(ctls):
        if ctl == 'U':
            ctls[b_start] = 'b'
            for t_start, t_end in _get_text_blocks(snapshot, b_start, b_end):
                ctls[t_start] = 't'
                if t_end < b_end:
                    ctls[t_end] = 'b'

    # (7) Mark data blocks of all zeroes with 's'
    for ctl, b_start, b_end in _get_blocks(ctls):
        if ctl == 'b' and sum(snapshot[b_start:b_end]) == 0:
            ctls[b_start] = 's'

    return ctls

def _generate_ctls_without_code_map(snapshot, start, end):
    ctls = {start: 'c', end: 'i'}

    # Look for potential 'RET', 'JR d' and 'JP nn' instructions and assume that
    # they end a block (after which another block follows); note that we don't
    # bother examining the final byte because no block can follow it
    for address in range(start, end - 1):
        b = snapshot[address]
        if b == 201:
            ctls[address + 1] = 'c'
        elif b == 195 and address < end - 3:
            ctls[address + 3] = 'c'
        elif b == 24 and address < end - 2:
            ctls[address + 2] = 'c'

    ctl_parser = CtlParser(ctls)
    disassembly = Disassembly(snapshot, ctl_parser)

    # Scan the disassembly for pairs of adjacent blocks that overlap, and join
    # such pairs
    while True:
        done = True
        for entry in disassembly.entries[:-1]:
            if entry.bad_blocks:
                del ctls[entry.next.address]
                disassembly.remove_entry(entry.address)
                disassembly.remove_entry(entry.next.address)
                done = False
        if done:
            break
        disassembly.build()

    # Scan the disassembly for blocks that don't end in a 'RET', 'JP nn' or
    # 'JR d' instruction, and join them to the next block
    changed = False
    for entry in disassembly.entries[:-1]:
        last_instr = entry.instructions[-1].operation
        if last_instr != 'RET' and not (last_instr[:2] in ('JP', 'JR') and last_instr[3:].isdigit()):
            next_address = entry.next.address
            if next_address < end:
                del ctls[entry.next.address]
                disassembly.remove_entry(entry.address)
                disassembly.remove_entry(entry.next.address)
                changed = True
    if changed:
        disassembly.build()

    # Scan the disassembly for pairs of adjacent blocks where the start address
    # of the second block is JRed or JPed to from the first block, and join
    # such pairs
    while True:
        done = True
        for entry in disassembly.entries[:-1]:
            for instruction in entry.instructions:
                operation = instruction.operation
                if operation[:2] in ('JR', 'JP') and operation[-5:] == str(entry.next.address):
                    del ctls[entry.next.address]
                    disassembly.remove_entry(entry.address)
                    disassembly.remove_entry(entry.next.address)
                    done = False
                    break
        if done:
            break
        disassembly.build()

    # Mark a NOP sequence at the beginning of a block as a separate zero block
    for entry in disassembly.entries:
        ctls[entry.address] = 's'
        for instruction in entry.instructions:
            if instruction.operation != 'NOP':
                ctls[instruction.address] = 'c'
                break

    # See which blocks marked as code look like text or data
    _analyse_blocks(disassembly, ctls)

    return ctls

def write_ctl(ctlfile, ctls, ctl_hex):
    # Write a control file
    addr_fmt = get_address_format(ctl_hex, ctl_hex == 1)
    with open(ctlfile, 'w') as f:
        start = addr_fmt.format(min(ctls))
        f.write('@ {} {}\n'.format(start, AD_START))
        f.write('@ {} {}\n'.format(start, AD_ORG))
        for address in [a for a in sorted(ctls) if a < 65536]:
            f.write('{0} {1}\n'.format(ctls[address], addr_fmt.format(address)))

def _check_for_data(snapshot, start, end):
    size = end - start
    if size > 3:
        count = 1
        prev_b = snapshot[start]
        for a in range(start + 1, end):
            b = snapshot[a]
            if b == prev_b:
                count += 1
                if count > 3:
                    return True
            else:
                count = 1
                prev_b = b
    if size > 9:
        d = len(set(snapshot[start:end]))
        return d < size * UNIQUE_BYTES_MAX

def _check_text(t_blocks, t_start, t_end, letters, punc):
    length = t_end - t_start
    if length >= MIN_LENGTH and len(set(letters)) >= length * UNIQUE_CHARS_MIN and len(punc) <= length * PUNC_CHARS_MAX:
        t_block = [t_start, t_end]
        if t_blocks:
            prev_t_block = t_blocks[-1]
            if prev_t_block[1] + TEXT_GAP_MAX >= t_start:
                # If the previous t-block is close to this one, merge them
                prev_t_block[1] = t_end
            else:
                t_blocks.append(t_block)
        else:
            t_blocks.append(t_block)

def _get_text_blocks(snapshot, start, end):
    t_blocks = []
    if end - start >= MIN_LENGTH:
        letters = []
        punc = []
        t_start = None
        for address in range(start, end):
            char = chr(snapshot[address])
            if char in CHARS:
                if char in PUNC_CHARS:
                    punc.append(char)
                else:
                    letters.append(char)
                if t_start is None:
                    t_start = address
            else:
                if t_start:
                    _check_text(t_blocks, t_start, address, letters, punc)
                letters[:] = []
                punc[:] = []
                t_start = None
        if t_start:
            _check_text(t_blocks, t_start, end, letters, punc)
    return t_blocks

def _get_blocks(ctls):
    # Determine the block start and end addresses
    blocks = [[ctls[address], address, None] for address in sorted(ctls)]
    for i, block in enumerate(blocks[1:]):
        blocks[i][2] = block[1]
    blocks.pop()
    return blocks

def _analyse_blocks(disassembly, ctls):
    snapshot = disassembly.disassembler.snapshot

    # See which blocks marked as code look like text or data
    while 1:
        done = True
        for ctl, start, end in _get_blocks(ctls):
            if ctl == 'c':
                text_blocks = _get_text_blocks(snapshot, start, end)
                if text_blocks:
                    for t_start, t_end in text_blocks:
                        ctls[t_start] = 't'
                        ctls[t_end] = 'c'
                    disassembly.remove_entry(start)
                    done = False
                elif _check_for_data(snapshot, start, end):
                    ctls[start] = 'b'
                    disassembly.remove_entry(start)
                else:
                    # This block is unidentified (it doesn't look like text or
                    # data); mark it with an 'X' so that we don't examine it
                    # again
                    ctls[start] = 'X'
        if done:
            break

    # Relabel the unidentified blocks as code
    for address, ctl in ctls.items():
        if ctl == 'X':
            ctls[address] = 'c'

    # Scan the disassembly for pairs of adjacent blocks that overlap, and mark
    # the first block in each pair as data; also mark code blocks that have no
    # terminal instruction as data
    disassembly.build()
    for entry in disassembly.entries:
        if entry.bad_blocks or (ctls[entry.address] == 'c' and not _is_terminal_instruction(entry.instructions[-1])):
            ctls[entry.address] = 'b'

    # Mark a NOP sequence at the beginning of a code block as a zero block
    for ctl, start, end in _get_blocks(ctls):
        if ctl == 'c':
            ctls[start] = 's'
            for address in range(start, end):
                if snapshot[address]:
                    ctls[address] = 'c'
                    break

def generate_ctls(snapshot, start, end, code_map):
    if code_map:
        ctls = _generate_ctls_with_code_map(snapshot, start, end, code_map)
    else:
        ctls = _generate_ctls_without_code_map(snapshot, start, end)

    # Join any adjacent data and zero blocks
    blocks = _get_blocks(ctls)
    prev_block = blocks[0]
    for block in blocks[1:]:
        if prev_block[0] in 'bs' and block[0] in 'bs':
            ctls[prev_block[1]] = 'b'
            del ctls[block[1]]
        else:
            prev_block = block

    return ctls

class Entry:
    def __init__(self, header, title, description, ctl, blocks, registers,
                 end_comment, footer, asm_directives, ignoreua_directives):
        self.header = header
        self.title = title
        self.ctl = ctl
        self.blocks = blocks
        self.instructions = []
        for block in blocks:
            for instruction in block.instructions:
                instruction.entry = self
                self.instructions.append(instruction)
        first_instruction = self.instructions[0]
        first_instruction.ctl = ctl
        self.registers = registers
        self.end_comment = end_comment
        self.footer = footer
        self.asm_directives = asm_directives
        self.ignoreua_directives = ignoreua_directives
        self.address = first_instruction.address
        self.description = description
        self.next = None
        self.bad_blocks = []
        for block in self.blocks:
            last_instruction = block.instructions[-1]
            if last_instruction.address + last_instruction.size() > block.end:
                self.bad_blocks.append(block)

    def width(self):
        return max([len(i.operation) for i in self.instructions])

    def has_ignoreua_directive(self, comment_type):
        return comment_type in self.ignoreua_directives

class Disassembly:
    def __init__(self, snapshot, ctl_parser, config=None, final=False, defb_size=8, defb_mod=1,
                 zfill=False, defm_width=66, asm_hex=False, asm_lower=False):
        ctl_parser.apply_asm_data_directives(snapshot)
        self.disassembler = Disassembler(snapshot, defb_size, defb_mod, zfill, defm_width, asm_hex, asm_lower)
        self.ctl_parser = ctl_parser
        if asm_hex:
            if asm_lower:
                self.address_fmt = '{0:04x}'
            else:
                self.address_fmt = '{0:04X}'
        else:
            self.address_fmt = '{0}'
        self.entry_map = {}
        self.config = config or {}
        self.build(final)

    def build(self, final=False):
        self.instructions = {}
        self.entries = []
        self._create_entries()
        if self.entries:
            self.org = self.entries[0].address
        else:
            self.org = None
        if final:
            self._calculate_references()

    def _create_entries(self):
        for block in self.ctl_parser.get_blocks():
            if block.start in self.entry_map:
                entry = self.entry_map[block.start]
                self.entries.append(entry)
                for instruction in entry.instructions:
                    self.instructions[instruction.address] = instruction
                continue
            title = block.title
            if not title:
                ctl = block.ctl
                if ctl != 'i' or block.description or block.registers or block.blocks[0].header:
                    name = 'Title-' + ctl
                    title = format_template(self.config.get(name, ''), name, address=self._address_str(block.start))
            for sub_block in block.blocks:
                address = sub_block.start
                if sub_block.ctl in 'cBT':
                    base = sub_block.sublengths[0][1]
                    instructions = self.disassembler.disassemble(sub_block.start, sub_block.end, base)
                elif sub_block.ctl in 'bgstuw':
                    sublengths = sub_block.sublengths
                    if sublengths[0][0]:
                        if sub_block.ctl == 's':
                            length = sublengths[0][0]
                        else:
                            length = sum([s[0] for s in sublengths])
                    else:
                        length = sub_block.end - sub_block.start
                    instructions = []
                    while address < sub_block.end:
                        end = min(address + length, sub_block.end)
                        if sub_block.ctl == 't':
                            instructions += self.disassembler.defm_range(address, end, sublengths)
                        elif sub_block.ctl == 'w':
                            instructions += self.disassembler.defw_range(address, end, sublengths)
                        elif sub_block.ctl == 's':
                            instructions.append(self.disassembler.defs(address, end, sublengths))
                        else:
                            instructions += self.disassembler.defb_range(address, end, sublengths)
                        address += length
                else:
                    instructions = self.disassembler.ignore(sub_block.start, sub_block.end)
                self._add_instructions(sub_block, instructions)

            sub_blocks = []
            i = 0
            while i < len(block.blocks):
                sub_block = block.blocks[i]
                i += 1
                sub_blocks.append(sub_block)
                if sub_block.multiline_comment is not None:
                    end, sub_block.comment = sub_block.multiline_comment
                    while i < len(block.blocks) and block.blocks[i].start < end:
                        next_sub_block = block.blocks[i]
                        sub_block.instructions += next_sub_block.instructions
                        sub_block.end = next_sub_block.end
                        i += 1

            entry = Entry(block.header, title, block.description, block.ctl, sub_blocks,
                          block.registers, block.end_comment, block.footer, block.asm_directives,
                          block.ignoreua_directives)
            self.entry_map[entry.address] = entry
            self.entries.append(entry)
        for i, entry in enumerate(self.entries[1:]):
            self.entries[i].next = entry

    def remove_entry(self, address):
        if address in self.entry_map:
            del self.entry_map[address]

    def _add_instructions(self, sub_block, instructions):
        sub_block.instructions = instructions
        for instruction in instructions:
            self.instructions[instruction.address] = instruction
            instruction.asm_directives = sub_block.asm_directives.get(instruction.address, ())
            instruction.label = None
            for asm_dir in instruction.asm_directives:
                if asm_dir.startswith(AD_LABEL + '='):
                    instruction.label = asm_dir[6:]
                    if instruction.label.startswith('*'):
                        instruction.ctl = '*'
                    break

    def _calculate_references(self):
        for entry in self.entries:
            for instruction in entry.instructions:
                instruction.referrers = []
        for entry in self.entries:
            for instruction in entry.instructions:
                operation = instruction.operation
                if operation.upper().startswith(('DJ', 'JR', 'JP', 'CA', 'RS')):
                    addr_str = get_address(operation)
                    if addr_str:
                        callee = self.instructions.get(parse_int(addr_str))
                        if callee and (entry.ctl != 'u' or callee.entry == entry) and callee.label != '':
                            callee.add_referrer(entry)

    def _address_str(self, address):
        return self.address_fmt.format(address)

class SkoolWriter:
    def __init__(self, snapshot, ctl_parser, options, config):
        self.comment_width = max(options.line_width - 2, MIN_COMMENT_WIDTH)
        self.asm_hex = options.base == 16
        self.disassembly = Disassembly(snapshot, ctl_parser, config, True, config['DefbSize'], config['DefbMod'],
                                       config['DefbZfill'], config['DefmSize'], self.asm_hex, options.case == 1)
        self.address_fmt = get_address_format(self.asm_hex, options.case == 1)
        self.config = config

    def address_str(self, address, pad=True):
        if self.asm_hex or pad:
            return self.address_fmt.format(address)
        return str(address)

    def write_skool(self, write_refs, text):
        for entry_index, entry in enumerate(self.disassembly.entries):
            if entry_index:
                write_line('')
            self._write_entry(entry, write_refs, text)

    def _write_entry(self, entry, write_refs, show_text):
        if entry.header:
            for line in entry.header:
                write_line(line)
            write_line('')

        self.write_asm_directives(*entry.asm_directives)
        if entry.has_ignoreua_directive(TITLE):
            self.write_asm_directives(AD_IGNOREUA)

        if entry.ctl == 'i' and entry.blocks[-1].end >= 65536 and not entry.title and all([b.ctl == 'i' for b in entry.blocks]):
            return

        for block in entry.bad_blocks:
            warn('Code block at {} overlaps the following block at {}'.format(self.address_str(block.start, False), self.address_str(block.end, False)))

        if entry.title:
            self.write_comment(entry.title)
            wrote_desc = self._write_entry_description(entry, write_refs)
            if entry.registers:
                if not wrote_desc:
                    self._write_empty_paragraph()
                    wrote_desc = True
                self._write_registers(entry)
        else:
            wrote_desc = False

        self._write_body(entry, wrote_desc, write_refs, show_text)

        if entry.has_ignoreua_directive(END):
            self.write_asm_directives(AD_IGNOREUA)
        self.write_paragraphs(entry.end_comment)

        if entry.footer:
            write_line('')
            for line in entry.footer:
                write_line(line)

    def _write_entry_description(self, entry, write_refs):
        wrote_desc = False
        ignoreua_d = entry.has_ignoreua_directive(DESCRIPTION)
        if write_refs:
            referrers = entry.instructions[0].referrers
            if referrers and (write_refs == 2 or not entry.description):
                self.write_comment('')
                if ignoreua_d:
                    self.write_asm_directives(AD_IGNOREUA)
                self.write_referrers(referrers, False)
                wrote_desc = True
        if entry.description:
            if wrote_desc:
                self._write_paragraph_separator()
            else:
                self.write_comment('')
                if ignoreua_d:
                    self.write_asm_directives(AD_IGNOREUA)
            self.write_paragraphs(entry.description)
            wrote_desc = True
        return wrote_desc

    def _write_registers(self, entry):
        self.write_comment('')
        if entry.has_ignoreua_directive(REGISTERS):
            self.write_asm_directives(AD_IGNOREUA)
        max_indent = max([reg.find(':') for reg, desc in entry.registers])
        for reg, desc in entry.registers:
            reg = reg.rjust(max_indent + len(reg) - reg.find(':'))
            if desc:
                desc_indent = len(reg) + 1
                desc_lines = wrap(desc, max(self.comment_width - desc_indent, MIN_COMMENT_WIDTH))
                write_line('; {} {}'.format(reg, desc_lines[0]))
                desc_prefix = '.'.ljust(desc_indent)
                for line in desc_lines[1:]:
                    write_line('; {}{}'.format(desc_prefix, line))
            else:
                write_line('; {}'.format(reg))

    def _format_block_comment(self, block, width):
        rowspan = len(block.instructions)
        comment = block.comment
        multi_line = rowspan > 1 and comment
        if multi_line and not comment.replace('.', ''):
            comment = comment[1:]
        if multi_line or comment.startswith('{'):
            balance = comment.count('{') - comment.count('}')
            if multi_line and balance < 0:
                opening = '{' * (1 - balance)
            else:
                opening = '{'
            if comment.startswith('{'):
                opening = opening + ' '
            closing = '}' * max(1 + balance, 1)
            if comment.endswith('}'):
                closing = ' ' + closing
            comment_lines = wrap(opening + comment, width)
            if len(comment_lines) < rowspan:
                comment_lines.extend([''] * (rowspan - len(comment_lines) - 1))
                comment_lines.append(closing.lstrip())
            elif len(comment_lines[-1]) + len(closing) <= width:
                comment_lines[-1] += closing
            else:
                comment_lines.append(closing.lstrip())
            return comment_lines
        return wrap(comment, width)

    def _write_body(self, entry, wrote_desc, write_refs, show_text):
        op_width = max((OP_WIDTH, entry.width()))
        line_width = op_width + 8
        first_block = True
        for block in entry.blocks:
            ignoreua_m = block.has_ignoreua_directive(block.start, MID_BLOCK)
            begun_header = False
            if not first_block and entry.ctl == 'c' and write_refs:
                referrers = block.instructions[0].referrers
                if referrers and (write_refs == 2 or not block.header):
                    if ignoreua_m:
                        self.write_asm_directives(AD_IGNOREUA)
                    self.write_referrers(referrers)
                    begun_header = True
            if block.header:
                if first_block:
                    if not wrote_desc:
                        self._write_empty_paragraph()
                    if not entry.registers:
                        self._write_empty_paragraph()
                    self.write_comment('')
                if begun_header:
                    self._write_paragraph_separator()
                elif ignoreua_m:
                    self.write_asm_directives(AD_IGNOREUA)
                self.write_paragraphs(block.header)
            comment_width = max(self.comment_width - line_width, MIN_INSTRUCTION_COMMENT_WIDTH)
            comment_lines = self._format_block_comment(block, comment_width)
            self._write_instructions(entry, block, op_width, comment_lines, write_refs, show_text)
            indent = ' ' * line_width
            for j in range(len(block.instructions), len(comment_lines)):
                write_line('{}; {}'.format(indent, comment_lines[j]))
            first_block = False

    def _write_instructions(self, entry, block, op_width, comment_lines, write_refs, show_text):
        index = 0
        for instruction in block.instructions:
            ctl = instruction.ctl or ' '
            address = instruction.address
            operation = instruction.operation
            if block.comment:
                comment = comment_lines[index]
            elif show_text and entry.ctl != 't':
                comment = self.to_ascii(instruction.bytes)
            else:
                comment = ''
            if index > 0 and entry.ctl == 'c' and ctl == '*' and write_refs:
                self.write_referrers(instruction.referrers)
            self.write_asm_directives(*instruction.asm_directives)
            if block.has_ignoreua_directive(instruction.address, INSTRUCTION):
                self.write_asm_directives(AD_IGNOREUA)
            if entry.ctl == 'c' or comment or block.comment:
                write_line(('{}{} {} ; {}'.format(ctl, self.address_str(address), operation.ljust(op_width), comment)).rstrip())
            else:
                write_line(('{}{} {}'.format(ctl, self.address_str(address), operation)).rstrip())
            index += 1

    def write_comment(self, text):
        if text:
            for line in self.wrap(text):
                write_line('; {0}'.format(line))
        else:
            write_line(';')

    def _write_empty_paragraph(self):
        self.write_comment('')
        self.write_comment('.')

    def _write_paragraph_separator(self):
        self.write_comment('.')

    def write_paragraphs(self, paragraphs):
        if paragraphs:
            for p in paragraphs[:-1]:
                self.write_comment(p)
                self._write_paragraph_separator()
            self.write_comment(paragraphs[-1])

    def write_referrers(self, referrers, erefs=True):
        if referrers:
            if erefs:
                key = 'EntryPointRef'
            else:
                key = 'Ref'
            fields = {'ref': '#R' + self.address_str(referrers[-1].address, False)}
            if len(referrers) > 1:
                key += 's'
                fields['refs'] = ', '.join(['#R' + self.address_str(r.address, False) for r in referrers[:-1]])
            self.write_comment(format_template(self.config[key], key, **fields))

    def write_asm_directives(self, *directives):
        for directive in directives:
            write_line('@' + directive)

    def to_ascii(self, data):
        chars = ['[']
        for b in data:
            if 32 <= b < 127:
                chars.append(chr(b))
            else:
                chars.append('.')
        chars.append(']')
        return ''.join(chars)

    def wrap(self, text):
        lines = []
        for line, wrap_flag in self.parse_blocks(text):
            if wrap_flag == 0:
                lines.append(line)
            elif wrap_flag == 1:
                lines.extend(wrap(line, self.comment_width))
            else:
                block = wrap(line, self.comment_width)
                lines.append(block[0])
                if len(block) > 1:
                    if block[0].endswith(' |'):
                        indent = 2
                    else:
                        indent = block[0].rfind(' | ') + 3
                    while indent < len(block[0]) and block[0][indent] == ' ':
                        indent += 1
                    pad = ' ' * indent
                    lines.extend(pad + line for line in wrap(' '.join(block[1:]), self.comment_width - indent))
        return lines

    def parse_block(self, text, begin):
        try:
            index = parse_brackets(text, begin)[0]
        except ClosingBracketError:
            raise SkoolKitError("No closing ')' on parameter list: {}...".format(text[begin:begin + 15]))
        try:
            index, flag = parse_brackets(text, index, '', '<', '>')
        except ClosingBracketError:
            raise SkoolKitError("No closing '>' on flags: {}...".format(text[index:index + 15]))
        wrap_flag = 1
        if flag == 'nowrap':
            wrap_flag = 0
        elif flag == 'wrapalign':
            wrap_flag = 2

        indexes = [(index, 1)]

        # Parse the table rows or list items
        while True:
            start = text.find('{ ', index)
            if start < 0:
                break
            try:
                end = text.index(' }', start)
            except ValueError:
                raise SkoolKitError("No closing ' }}' on row/item: {}...".format(text[start:start + 15]))
            index = end + 2
            indexes.append((index, wrap_flag))


        indexes.append((len(text), 1))
        return indexes

    def parse_blocks(self, text):
        markers = ((TABLE_MARKER, TABLE_END_MARKER), (UDGTABLE_MARKER, TABLE_END_MARKER), (LIST_MARKER, LIST_END_MARKER))
        indexes = []

        # Find table/list markers and row/item definitions
        index = 0
        while True:
            starts = [(marker[0], marker[1], text.find(marker[0], index)) for marker in markers]
            for marker, end_marker, start in starts:
                if start >= 0:
                    if start > 0:
                        indexes.append((start - 1, 1))
                    try:
                        end = text.index(end_marker, start) + len(end_marker)
                    except ValueError:
                        raise SkoolKitError("No end marker found: {}...".format(text[start:start + len(marker) + 15]))
                    indexes.extend(self.parse_block(text[:end], start + len(marker)))
                    break
            else:
                break
            index = indexes[-1][0] + 1

        if not indexes or indexes[-1][0] != len(text):
            indexes.append((len(text), 1))
        indexes.sort(key=lambda e: e[0])
        lines = []
        start = 0
        for end, wrap_flag in indexes:
            lines.append((text[start:end].strip(), wrap_flag))
            start = end
        return lines
