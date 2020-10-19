 #==========================================================================
# (c) 2019 Cirrus Logic, Inc.
#--------------------------------------------------------------------------
# Project : Convert from WMFW/WMDR ("BIN") Files to C Header/Source
# File    : firmware_converter.py
#--------------------------------------------------------------------------
# Licensed under the Apache License, Version 2.0 (the License); you may
# not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an AS IS BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#--------------------------------------------------------------------------
#
# Environment Requirements: None
#
#==========================================================================

#==========================================================================
# IMPORTS
#==========================================================================
import os
import sys
import argparse
from wmfw_parser import wmfw_parser, get_memory_region_from_type
from wmdr_parser import wmdr_parser
from firmware_exporter_factory import firmware_exporter_factory

#==========================================================================
# VERSION
#==========================================================================
VERSION_STRING = "3.0.0"

#==========================================================================
# CONSTANTS/GLOBALS
#==========================================================================
supported_part_numbers = ['cs35l41', 'cs40l25', 'cs40l30', 'cs48l32', 'cs47l63', 'cs47l66']
supported_commands = ['print', 'export', 'wisce', 'fw_img_v1', 'json']

supported_mem_maps = {
    'halo_type_0': {
        'parts': ['cs35l41', 'cs40l25', 'cs40l30', 'cs48l32', 'cs47l63', 'cs47l66'],
        'xm': {
            'u24': 0x2800000,
            'p32': 0x2000000,
            'u32': 0x2400000,
        },
        'ym': {
            'u24': 0x3400000,
            'p32': 0x2C00000,
            'u32': 0x3000000,
        },
        'pm': {
            'pm32': 0x3800000,
        }
    }
}

#==========================================================================
# CLASSES
#==========================================================================
class address_resolver:
    def __init__(self, part_number):
        for key in supported_mem_maps.keys():
            if (part_number in supported_mem_maps[key]['parts']):
                self.mem_map = supported_mem_maps[key]

        return

    def resolve(self, mem_region, mem_type, offset):
        address = None
        if ((mem_region in self.mem_map) and (mem_type in self.mem_map[mem_region])):
            if (mem_type == 'u24'):
                addresses_per_word = 4
            elif (mem_type == 'p32'):
                addresses_per_word = 3
            elif (mem_type == 'pm32'):
                addresses_per_word = 5

            address = self.mem_map[mem_region][mem_type] + offset * addresses_per_word

            # All addresses must be on 4-byte boundaries
            address = address & ~0x3

        return address

class block_list:
    def __init__(self, size_limit, address_resolver):
        self.size_limit = size_limit
        self.ar = address_resolver
        self.blocks = []

        return

    def rehash_blocks(self):
        new_blocks = []
        for block in self.blocks:
            temp_len = len(block[1])
            if (temp_len < self.size_limit):
                new_blocks.append((block[0], block[1]))
            else:
                temp_block = []
                temp_start_offset = block[0]
                for data_byte in block[1]:
                    temp_block.append(data_byte)
                    if (len(temp_block) >= self.size_limit):
                        new_blocks.append((temp_start_offset, temp_block))
                        temp_start_offset = temp_start_offset + len(temp_block)
                        temp_block = []
                if (len(temp_block) > 0):
                    new_blocks.append((temp_start_offset, temp_block))

        self.blocks = new_blocks

        return

class fw_block_list(block_list):
    def __init__(self, data_blocks, size_limit, address_resolver):
        block_list.__init__(self, size_limit, address_resolver)

        for block in data_blocks:
            temp_mem_region = get_memory_region_from_type(block.fields['type'])
            if (temp_mem_region != 'abs'):
                new_address = self.ar.resolve(temp_mem_region, block.memory_type, block.fields['start_offset'])
            else:
                new_address = block.fields['start_offset']
            self.blocks.append((new_address, block.data))
        return

class coeff_block_list(block_list):
    def __init__(self, data_blocks, size_limit, address_resolver, fw_id_block):
        block_list.__init__(self, size_limit, address_resolver)

        for block in data_blocks:
            temp_mem_region = get_memory_region_from_type(block.fields['type'])
            if (temp_mem_region != 'abs'):
                # Coefficient value data blocks in WMDR files offsets are in terms of External Port address rather than
                # in terms of algorithm memory region fields (i.e. XM/YM words), so calculation is different
                temp_offset = fw_id_block.get_adjusted_offset(block.fields['algorithm_identification'],
                                                              temp_mem_region,
                                                              0)
                new_address = self.ar.resolve(temp_mem_region, block.memory_type, temp_offset)
                new_address = new_address + block.fields['start_offset']
            else:
                new_address = block.fields['start_offset']
            self.blocks.append((new_address, block.data))

        return


#==========================================================================
# HELPER FUNCTIONS
#==========================================================================
def validate_environment():
    result = True

    return result

def get_args(args):
    """Parse arguments"""
    parser = argparse.ArgumentParser(description='Parse command line arguments')
    parser.add_argument(dest='command', type=str, choices=supported_commands, help='The command you wish to execute.')
    parser.add_argument(dest='part_number', type=str, choices=supported_part_numbers,
                        help='The part number that the wmfw is targeted at.')
    parser.add_argument(dest='wmfw', type=str,help='The wmfw (or \'firmware\') file to be parsed.')
    parser.add_argument('--wmdr', dest='wmdrs', type=str, nargs='*', help='The wmdr (or \'bin\') file(s) to be '\
                        'parsed.')
    parser.add_argument('-s', '--suffix', type=str, default='',
                        dest='suffix', help='Add a suffix to filenames, variables and defines.')
    parser.add_argument('-i', '--i2c-address', type=str, default='0x80', dest='i2c_address', help='Specify I2C address for WISCE script output.')
    parser.add_argument('-b', '--block-size-limit', type=int, default='4140', dest='block_size_limit', help='Specify maximum byte size of block per control port transaction.')
    parser.add_argument('--sym-input', dest='symbol_id_input', type=str, default=None, help='The location of the symbol table C header(s).  If not specified, a header is generated with all controls.')
    parser.add_argument('--sym-output', dest='symbol_id_output', type=str, default=None, help='The location of the output symbol table C header.  Only used when no --sym-input is specified.')
    parser.add_argument('--binary', dest='binary_output', action="store_true", help='Request binary fw_img output format.')
    parser.add_argument('--wmdr-only', dest='wmdr_only', action="store_true", help='Request to ONLY store WMDR files in fw_img.')
    parser.add_argument('--generic-sym', dest='generic_sym', action="store_true", help='Use generic algorithm name for \'FIRMWARE_*\' algorithm controls')

    return parser.parse_args(args[1:])

def validate_args(args):
    # Check that WMFW path exists
    if (not os.path.exists(args.wmfw)):
        print("Invalid wmfw path: " + args.wmfw)
        return False
    if (args.wmdrs is not None):
        # Check that WMDR path(s) exists
        for wmdr in args.wmdrs:
            if (not os.path.exists(wmdr)):
                print("Invalid wmdr path: " + wmdr)
                return False

    # Check that all symbol id header files exist
    if ((args.command == 'fw_img_v1') and (args.symbol_id_input is not None)):
        if (not os.path.exists(args.symbol_id_input)):
            print("Invalid Symbol Header path: " + args.symbol_id_input)
            return False


    # Check that block_size_limit <= 4140
    if (args.block_size_limit > 4140):
        print("Invalid block_size_limit: " + str(args.block_size_limit))
        print("Must be 4140 bytes or less.")
        return False

    return True

def print_start():
    print("")
    print("firmware_converter")
    print("Convert from WMFW/WMDR (\"BIN\") Files to C Header/Source")
    print("Version " + VERSION_STRING)

    return

def print_args(args):
    print("")
    print("Command: " + args.command)
    print("Part Number: " + args.part_number)
    print("WMFW Path: " + args.wmfw)
    if (args.wmdrs is not None):
        for wmdr in args.wmdrs:
            print("WMDR Path: " + wmdr)

    if (args.suffix):
        print("Suffix: " + args.suffix)
    else:
        print("No suffix")

    if (args.command == 'fw_img_v1'):
        if (args.symbol_id_input is not None):
            print("Input Symbol ID Header: " + args.symbol_id_input)
        else:
            print("Input Symbol ID Header: None")

        if (args.symbol_id_output is not None):
            print("Output Symbol ID Header: " + args.symbol_id_output)

    return

def print_results(results_string):
    print(results_string)

    return

def print_end():
    print("Exit.")

    return

def error_exit(error_message):
    print('ERROR: ' + error_message)
    exit(1)

#==========================================================================
# MAIN PROGRAM
#==========================================================================
def main(argv):

    print_start()

    if (not (validate_environment())):
        error_exit("Invalid Environment")

    args = get_args(argv)

    # validate arguments
    print_args(args)
    if (not (validate_args(args))):
        error_exit("Invalid Arguments")

    if (args.wmdrs is not None):
        process_wmdr = True
    else:
        process_wmdr = False

    # Parse WMFW and WMDR files
    wmfw = wmfw_parser(args.wmfw)
    wmfw.parse()

    wmdrs = []
    if (process_wmdr):
        for wmdr_filename in args.wmdrs:
            wmdr = wmdr_parser(wmdr_filename)
            wmdr.parse()
            wmdrs.append(wmdr)

    suffix = ""
    if (args.suffix):
        suffix = "_" + args.suffix

    # Create address resolver
    res = address_resolver(args.part_number)

    # Create firmware data blocks - size according to 'block_size_limit'
    fw_data_block_list = fw_block_list(wmfw.get_data_blocks(), args.block_size_limit, res)
    fw_data_block_list.rehash_blocks()

    # Create coeff data blocks - size according to 'block_size_limit'
    coeff_data_block_lists = []
    if (process_wmdr):
        for wmdr in wmdrs:
            coeff_data_block_list = coeff_block_list(wmdr.data_blocks,
                                                     args.block_size_limit,
                                                     res,
                                                     wmfw.fw_id_block)
            coeff_data_block_list.rehash_blocks()
            coeff_data_block_lists.append(coeff_data_block_list)

    # Create firmware exporter factory
    attributes = dict()
    attributes['part_number_str'] = args.part_number
    attributes['fw_meta'] = dict(fw_id = wmfw.fw_id_block.fields['firmware_id'], fw_rev = wmfw.fw_id_block.fields['firmware_revision'])
    attributes['suffix'] = suffix
    attributes['symbol_id_input'] = args.symbol_id_input
    attributes['i2c_address'] = args.i2c_address
    attributes['binary_output'] = args.binary_output
    attributes['wmdr_only'] = args.wmdr_only
    attributes['symbol_id_output'] = args.symbol_id_output
    f = firmware_exporter_factory(attributes)

    # Based on command, add firmware exporters
    if (args.command == 'export'):
        f.add_firmware_exporter('c_array')
    elif (args.command == 'fw_img_v1'):
        f.add_firmware_exporter('fw_img_v1')
    elif (args.command == 'wisce'):
        f.add_firmware_exporter('wisce')
    elif (args.command == 'json'):
        f.add_firmware_exporter('json')

    # Update block info based on any WMDR present
    if (not process_wmdr):
        f.update_block_info(len(fw_data_block_list.blocks), None)
    else:
        coeff_data_block_list_lengths = []
        for coeff_data_block_list in coeff_data_block_lists:
            coeff_data_block_list_lengths.append(len(coeff_data_block_list.blocks))
        f.update_block_info(len(fw_data_block_list.blocks), coeff_data_block_list_lengths)

    # Add controls
    # For each algorithm information data block
    for alg_block in wmfw.get_algorithm_information_data_blocks():
        # For each 'coefficient_descriptor', create control name and resolve address
        for coeff_desc in alg_block.fields['coefficient_descriptors']:
            temp_mem_region = get_memory_region_from_type(coeff_desc.fields['type'])
            temp_coeff_offset = wmfw.fw_id_block.get_adjusted_offset(alg_block.fields['algorithm_id'],
                                                                     temp_mem_region,
                                                                     coeff_desc.fields['start_offset'])
            temp_coeff_address = res.resolve(temp_mem_region, 'u24', temp_coeff_offset)

            # If generic_sym CLI argument specified and this is the 'general' algorith, replace the algorithm name
            if ((args.generic_sym) and (alg_block.fields['algorithm_id'] == wmfw.fw_id_block.fields['firmware_id'])):
                algorithm_name = "FIRMWARE"
            else:
                algorithm_name = alg_block.fields['algorithm_name']

            # If this is the 'struct_t' control, it's full name doesn't have the algo name, so add it
            if ('_struct_t' in coeff_desc.fields['coefficient_name']):
                control_name = algorithm_name + "_" + coeff_desc.fields['coefficient_name']
            else:
                control_name = coeff_desc.fields['full_coefficient_name'].replace(alg_block.fields['algorithm_name'], algorithm_name)

            # Add control
            f.add_control(algorithm_name,
                          alg_block.fields['algorithm_id'],
                          control_name,
                          temp_coeff_address)

    # Add metadata text
    metadata_text_lines = []
    metadata_text_lines.append('firmware_converter.py version: ' + VERSION_STRING)
    temp_line = ''
    for arg in argv:
        temp_line = temp_line + ' ' + arg
    metadata_text_lines.append('Command: ' + temp_line)
    for wmdr in wmdrs:
        if (len(wmdr.informational_text_blocks) > 0):
            metadata_text_lines.append('BIN Filename: ' + wmdr.filename)
            metadata_text_lines.append('    Informational Text:')
            for block in wmdr.informational_text_blocks:
                for line in block.text.splitlines():
                    metadata_text_lines.append('    ' + line)
            metadata_text_lines.append('')

    for line in metadata_text_lines:
        f.add_metadata_text_line(line)

    # Add FW Blocks
    for block in fw_data_block_list.blocks:
        block_bytes = []
        for byte_str in block[1]:
            block_bytes.append(int.from_bytes(byte_str, 'little', signed=False))

        f.add_fw_block(block[0], block_bytes)

    # Add Coeff Blocks
    if (process_wmdr):
        coeff_block_list_count = 0
        for coeff_data_block_list in coeff_data_block_lists:
            for block in coeff_data_block_list.blocks:
                # Convert from list of bytestring to list of int
                block_bytes = []
                for byte_str in block[1]:
                    block_bytes.append(int.from_bytes(byte_str, 'little', signed=False))

                f.add_coeff_block(coeff_block_list_count, block[0], block_bytes)

            coeff_block_list_count = coeff_block_list_count + 1

    results_str = ''
    if (args.command == 'print'):
        results_str = '\n'
        results_str = results_str + 'WMFW File: ' + args.wmfw + '\n'
        results_str = results_str + str(wmfw) + "\n"
        if (process_wmdr):
            wmdr_count = 0
            for wmdr in wmdrs:
                results_str = results_str + 'WMDR File: ' + args.wmdrs[wmdr_count] + '\n'
                results_str = results_str + str(wmdr)
                wmdr_count = wmdr_count + 1
    else:
        results_str = f.to_file()

    print_results(results_str)

    print_end()

    return

if __name__ == "__main__":
    main(sys.argv)
