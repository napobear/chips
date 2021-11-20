import yaml, copy
from string import Template

FIRST_DECODER_STEP = 22
DESC_PATH  = 'z80_desc.yml'
TEMPL_PATH = 'z80x.template.h'
OUT_PATH   = '../chips/z80x.h'
TAB_WIDTH  = 4

# a machine cycle description
class MCycle:
    def __init__(self, type, tcycles, items):
        self.type = type
        self.tcycles = tcycles
        self.items = items

# an opcode description
class Op:
    def __init__(self, name, cond, flags):
        self.name = name
        self.cond = cond
        self.cond_compiled = compile(cond, '<string>', 'eval')
        self.flags = flags
        self.opcode = -1
        self.prefix = ''
        self.single = False
        self.num_cycles = 0
        self.num_steps = 0
        self.decoder_offset = 0
        self.first_op_index = -1
        self.mcycles = []

OP_PATTERNS = []

OP_INDEX_CB = 512
OP_INDEX_CBHL = 513
OP_INDEX_DDFDCB = 514
OP_INDEX_INT_IM0 = 515
OP_INDEX_INT_IM1 = 516
OP_INDEX_INT_IM2 = 517
OP_INDEX_NMI = 518

NUM_SPECIAL_OPS = 7

# 0..255:   core opcodes
# 256..511: ED prefix opcodes
# 512..514: special decoder blocks for CB-prefix
# 515..519: special decoder blocks for interrupt handling
OPS = [None for _ in range(0,2*256 + NUM_SPECIAL_OPS)]

# a fetch machine cycle is processed as 2 parts because it overlaps
# with the 'action' of the previous instruction
FETCH_TCYCLES = 3
OVERLAPPED_FETCH_TCYCLES = 1
MEM_TCYCLES = 3
IO_TCYCLES = 4

# register mapping tables, see: http://www.z80.info/decoding.htm
r_comment   = [ 'b', 'c', 'd', 'e', 'h', 'l', '(hl)', 'a' ]
rp_comment  = [ 'bc', 'de', 'hl', 'sp' ]
rp2_comment = [ 'bc', 'de', 'hl', 'af' ]
alu_comment = [ 'add', 'adc', 'sub', 'sbc', 'and', 'xor', 'or', 'cp' ]
rot_comment = [ 'rlc', 'rrc', 'rl', 'rr', 'sla', 'sra', 'sll', 'srl' ]
cc_comment  = [ 'nz', 'z', 'nc', 'c', 'po', 'pe', 'p', 'm' ]

r_map    = [ 'cpu->b', 'cpu->c', 'cpu->d', 'cpu->e', 'cpu->hlx[cpu->hlx_idx].h', 'cpu->hlx[cpu->hlx_idx].l', 'XXX', 'cpu->a' ]
rr_map   = [ 'cpu->b', 'cpu->c', 'cpu->d', 'cpu->e', 'cpu->h', 'cpu->l', 'XXX', 'cpu->a' ]
rp_map   = [ 'cpu->bc', 'cpu->de', 'cpu->hlx[cpu->hlx_idx].hl', 'cpu->sp' ]
rrp_map  = [ 'cpu->bc', 'cpu->de', 'cpu->hl', 'cpu->sp' ]
rpl_map  = [ 'cpu->c', 'cpu->e', 'cpu->hlx[cpu->hlx_idx].l', 'cpu->spl']
rrpl_map = [ 'cpu->c', 'cpu->e', 'cpu->l', 'cpu->spl']
rph_map  = [ 'cpu->b', 'cpu->d', 'cpu->hlx[cpu->hlx_idx].h', 'cpu->sph']
rrph_map  = [ 'cpu->b', 'cpu->d', 'cpu->h', 'cpu->sph']
rp2_map  = [ 'cpu->bc', 'cpu->de', 'cpu->hlx[cpu->hlx_idx].hl', 'cpu->af' ]
rp2l_map = [ 'cpu->c', 'cpu->e', 'cpu->hlx[cpu->hlx_idx].l', 'cpu->f']
rp2h_map = [ 'cpu->b', 'cpu->d', 'cpu->hlx[cpu->hlx_idx].h', 'cpu->a']
cc_map   = [ '_cc_nz', '_cc_z', '_cc_nc', '_cc_c', '_cc_po', '_cc_pe', '_cc_p', '_cc_m' ]
alu_map  = [ '_z80_add8(cpu,', 
             '_z80_adc8(cpu,', 
             '_z80_sub8(cpu,',
             '_z80_sbc8(cpu,',
             '_z80_and8(cpu,',
             '_z80_xor8(cpu,',
             '_z80_or8(cpu,',
             '_z80_cp8(cpu,' ]
rot_map  = [ '_z80_rlc(cpu,',
             '_z80_rrc(cpu,',
             '_z80_rl(cpu,',
             '_z80_rr(cpu,',
             '_z80_sla(cpu,',
             '_z80_sra(cpu,',
             '_z80_sll(cpu,',
             '_z80_srl(cpu,' ]
im_map = [ '0', '0', '1', '2', '0', '0', '1', '2' ]

def err(msg: str):
    raise BaseException(msg)

def unwrap(maybe_value):
    if maybe_value is None:
        err('Expected valid value, found None')
    return maybe_value

# append a source code line
indent = 0
out_lines = ''

def tab():
    return ' ' * TAB_WIDTH * indent

def l(s) :
    global out_lines
    out_lines += tab() + s + '\n'

def map_comment(inp, y, z, p, q):
    return inp\
        .replace('$RY', r_comment[y])\
        .replace('$RZ', r_comment[z])\
        .replace('$ALU', alu_comment[y])\
        .replace('$ROT', rot_comment[y])\
        .replace('$RP2', rp2_comment[p])\
        .replace('$RP', rp_comment[p])\
        .replace('$CC-4', cc_comment[y-4])\
        .replace('$CC', cc_comment[y])\
        .replace('$IMY', im_map[y])\
        .replace('$Y*8', f'{y*8:X}h')\
        .replace('$Y', f'{y}')

def map_cpu(inp, y, z, p, q):
    return inp\
        .replace('$ADDR', 'cpu->addr')\
        .replace('$ALU(', alu_map[y])\
        .replace('$ROT(', rot_map[y])\
        .replace('$RRPL', rrpl_map[p])\
        .replace('$RRPH', rrph_map[p])\
        .replace('$RRP', rrp_map[p])\
        .replace('$RRY', rr_map[y])\
        .replace('$RRZ', rr_map[z])\
        .replace('$RY', r_map[y])\
        .replace('$RZ', r_map[z])\
        .replace('$RP2L', rp2l_map[p])\
        .replace('$RP2H', rp2h_map[p])\
        .replace('$RP2', rp2_map[p])\
        .replace('$RPL', rpl_map[p])\
        .replace('$RPH', rph_map[p])\
        .replace('$RP', rp_map[p])\
        .replace('$CC-4', cc_map[y-4])\
        .replace('$CC', cc_map[y])\
        .replace('$PCL', 'cpu->pcl')\
        .replace('$PCH', 'cpu->pch')\
        .replace('$PC', 'cpu->pc')\
        .replace('$AF', 'cpu->af')\
        .replace('$BC', 'cpu->bc')\
        .replace('$DE', 'cpu->de')\
        .replace('$HL', 'cpu->hlx[cpu->hlx_idx].hl')\
        .replace('$SP', 'cpu->sp')\
        .replace('$WZL', 'cpu->wzl')\
        .replace('$WZH', 'cpu->wzh')\
        .replace('$WZ', 'cpu->wz')\
        .replace('$DLATCH', 'cpu->dlatch')\
        .replace('$A', 'cpu->a')\
        .replace('$C', 'cpu->c')\
        .replace('$B', 'cpu->b')\
        .replace('$E', 'cpu->e')\
        .replace('$D', 'cpu->d')\
        .replace('$L', 'cpu->hlx[cpu->hlx_idx].l')\
        .replace('$H', 'cpu->hlx[cpu->hlx_idx].h')\
        .replace('$IMY', im_map[y])\
        .replace('$IM', 'cpu->im')\
        .replace('$Y*8', f'0x{y*8:02X}')\

def flag(op, flag):
    if flag in op.flags:
        return op.flags[flag]
    else:
        return False

def parse_opdescs():
    with open(DESC_PATH, 'r') as fp:
        desc = yaml.load(fp, Loader=yaml.FullLoader) # type: ignore
        for (op_name, op_desc) in desc.items():
            if 'cond' not in op_desc:
                op_desc['cond'] = 'True'
            if 'mcycles' not in op_desc:
                err(f"op '{op_name}' has no mcycles!")
            flags = {}
            if 'flags' in op_desc:
                flags = op_desc['flags']
            op = Op(op_name,op_desc['cond'], flags)
            if 'prefix' in op_desc:
                op.prefix = op_desc['prefix']
            num_fetch = 0
            num_overlapped = 0
            for mc_desc in op_desc['mcycles']:
                mc_type = mc_desc['type']
                if mc_type == 'fetch':
                    num_fetch += 1
                elif mc_type == 'overlapped':
                    num_overlapped += 1
                if 'tcycles' in mc_desc:
                    mc_tcycles = mc_desc['tcycles']
                else:
                    default_tcycles = {
                        'fetch': FETCH_TCYCLES,
                        'mread': MEM_TCYCLES,
                        'mwrite': MEM_TCYCLES,
                        'ioread': IO_TCYCLES,
                        'iowrite': IO_TCYCLES,
                        'overlapped': OVERLAPPED_FETCH_TCYCLES,
                        'generic': -1
                    }
                    mc_tcycles = default_tcycles[mc_type]
                if mc_tcycles == -1:
                    err(f'generic mcycles must have explicit length (op: {op_name})')
                if (mc_tcycles < 1) or (mc_tcycles > 7):
                    err(f'invalid mcycle len: {mc_tcycles}')
                if mc_type == 'fetch':
                    if mc_tcycles != 4:
                        err('fetch ticks must have exactly 4 tcycles')
                    mc_tcycles -= OVERLAPPED_FETCH_TCYCLES
                if mc_type == 'overlapped' and mc_tcycles != 1:
                    err('overlapped ticks must have exactly 1 tcycle!')
                mc = MCycle(mc_type, mc_tcycles, mc_desc)
                op.mcycles.append(mc)
            if num_fetch == 0:
                op.mcycles.insert(0, MCycle('fetch', FETCH_TCYCLES, {}))
            if num_overlapped == 0:
                op.mcycles.append(MCycle('overlapped', OVERLAPPED_FETCH_TCYCLES, {}))
            OP_PATTERNS.append(op)

def find_opdesc(name):
    for op_desc in OP_PATTERNS:
        if op_desc.name == name:
            return op_desc
    return None

def stampout_mcycle_items(mcycle_items, y, z, p, q):
    res = {}
    for key,val in mcycle_items.items():
        if type(val) == str:
            res[key] = map_cpu(val, y, z, p, q)
    return res

def stampout_op(prefix, opcode, op_index, op_desc):
    #  76 543 210
    # |xx|yyy|zzz|
    #    |ppq|
    y = (opcode >> 3) & 7
    z = opcode & 7
    p = y >> 1
    q = y & 1
    if op_desc.first_op_index == -1:
        op_desc.first_op_index = op_index
    op = copy.deepcopy(op_desc)
    op.name = map_comment(op.name, y, z, p, q)
    op.prefix = prefix
    op.opcode = opcode
    for mcycle in op.mcycles:
        mcycle.items = stampout_mcycle_items(mcycle.items, y, z, p, q)
    OPS[op_index] = op

def expand_optable():
    for oprange,prefix in enumerate(['', 'ed']):
        for opcode in range(0,256):
            x = opcode >> 6 # type: ignore (generated unused warning, but x is needed in 'eval')
            y = (opcode >> 3) & 7 
            z = opcode & 7 # type: ignore
            p = y >> 1 # type: ignore
            q = y & 1 # type: ignore
            op_index = oprange * 256 + opcode
            for op_desc_index,op_desc in enumerate(OP_PATTERNS):
                if not flag(op_desc, 'special'):
                    if eval(op_desc.cond_compiled) and op_desc.prefix == prefix:
                        if OPS[op_index] is not None:
                            err(f"Condition collission for opcode {op_desc_index:02X} and '{op_desc.name}'")
                        stampout_op(prefix, opcode, op_index, op_desc)
    stampout_op('cb', 0, OP_INDEX_CB, unwrap(find_opdesc('cb')))
    stampout_op('cb', 0, OP_INDEX_CBHL, unwrap(find_opdesc('cbhl')))
    stampout_op('cb', 0, OP_INDEX_DDFDCB, unwrap(find_opdesc('ddfdcb')))
    stampout_op('', 0, OP_INDEX_INT_IM0, unwrap(find_opdesc('int_im0')))
    stampout_op('', 0, OP_INDEX_INT_IM1, unwrap(find_opdesc('int_im1')))
    stampout_op('', 0, OP_INDEX_INT_IM2, unwrap(find_opdesc('int_im2')))
    stampout_op('', 0, OP_INDEX_NMI, unwrap(find_opdesc('nmi')))

# compute number of tcycles in an instruction
def compute_tcycles(op):
    cycles = 0
    for mcycle in op.mcycles:
        cycles += mcycle.tcycles
    return cycles

# generate code for one op
def gen_decoder():
    global indent
    indent = 2
    decoder_step = FIRST_DECODER_STEP

    def add(action):
        nonlocal decoder_step
        nonlocal step
        l(f'case {decoder_step:4}: {action}break;')
        decoder_step += 1
        step += 1
    
    for op_index,maybe_op in enumerate(OPS):
        op = unwrap(maybe_op)
        # ignore duplicate ops if they are flagged as 'single'
        if flag(op, 'single') and op.first_op_index != op_index:
            continue

        step = 0
        op.num_cycles = compute_tcycles(op)
        op.decoder_offset = decoder_step

        l('')
        l(f'// {op.prefix.upper()} {op.opcode:02X}: {op.name} (M:{len(op.mcycles)-1} T:{op.num_cycles})')
        for i,mcycle in enumerate(op.mcycles):
            action = (f"{mcycle.items['action']};" if 'action' in mcycle.items else '')
            if mcycle.type == 'fetch':
                pass
            elif mcycle.type == 'mread':
                l(f'// -- mread')
                addr = mcycle.items['ab']
                store = mcycle.items['dst'].replace('_X_', '_gd()')
                add('')
                add(f'_wait();_mread({addr});')
                add(f'{store}=_gd();{action}')
                for _ in range(3,mcycle.tcycles):
                    add('')
            elif mcycle.type == 'mwrite':
                l(f'// -- mwrite')
                addr = mcycle.items['ab']
                data = mcycle.items['db']
                add('')
                add(f'_wait();_mwrite({addr},{data});{action};')
                add('')
                for _ in range(3,mcycle.tcycles):
                    add('')
            elif mcycle.type == 'ioread':
                l(f'// -- ioread')
                addr = mcycle.items['ab']
                store = mcycle.items['dst'].replace('_X_', '_gd()')
                add('')
                add('')
                add(f'_wait();_ioread({addr});')
                add(f'{store}=_gd();{action}')
                for _ in range(4,mcycle.tcycles):
                    add('')
            elif mcycle.type == 'iowrite':
                l(f'// -- iowrite')
                addr = mcycle.items['ab']
                data = mcycle.items['db']
                add('')
                add(f'_iowrite({addr},{data});')
                add(f'_wait();{action};')
                add('')
                for _ in range(4,mcycle.tcycles):
                    add('')
            elif mcycle.type == 'generic':
                l(f'// -- generic')
                add(f'{action}')
                for _ in range(1,mcycle.tcycles):
                    add('')
            elif mcycle.type == 'overlapped':
                l(f'// -- overlapped')
                action = (f"{mcycle.items['action']};" if 'action' in mcycle.items else '')
                post_action = (f"{mcycle.items['post_action']};" if 'post_action' in mcycle.items else '')
                if 'prefix' in mcycle.items:
                    fetch = f"_fetch_{mcycle.items['prefix']}();"
                else:
                    fetch = '_fetch();'
                add(f'{action}{fetch}{post_action}')
        op.num_steps = step

def pip_table_to_string():
    global indent
    indent = 1
    res = ''
    for op_index,maybe_op in enumerate(OPS):
        op = unwrap(maybe_op)
        # map redundant 'single' ops to the original
        if flag(op, 'single') and op.first_op_index != op_index:
            op = unwrap(OPS[op.first_op_index])
        flags = ''
        if flag(op, 'indirect'):
            flags += '_Z80_OPSTATE_FLAGS_INDIRECT'
        if flag(op, 'imm8'):
            if flags != '':
                flags += '|'
            flags += '_Z80_OPSTATE_FLAGS_IMM8'
        if flags == '':
            flags = '0'
        res += tab() + f'{{ {op.decoder_offset-1:4}, {flags} }},' 
        res += f'  // {op.prefix.upper()} {op_index&0xFF:02X}: {op.name} (M:{len(op.mcycles)-1} T:{op.num_cycles} steps:{op.num_steps})\n'
    return res

def write_result():
    with open(TEMPL_PATH, 'r') as templf:
        templ = Template(templf.read())
        c_src = templ.safe_substitute(decode_block=out_lines, op_table_block=pip_table_to_string())
        with open(OUT_PATH, 'w') as outf:
            outf.write(c_src)

if __name__=='__main__':
    parse_opdescs()
    expand_optable()
    gen_decoder()
    write_result()
