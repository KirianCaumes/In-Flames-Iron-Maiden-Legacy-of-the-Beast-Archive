"""Disassemble il2cpp methods (ARM64) with symbol annotations from Il2CppDumper's script.json.
usage: il2cpp_disasm.py <libil2cpp.so> <script.json> <MethodName> [...]   (names like 'PlayParticlesFromAnimationState$$Update')"""
import sys, json, pickle, os, re
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
so, sj, *names = sys.argv[1:]
cache = sj + '.pkl'
if os.path.exists(cache):
    meth, meta = pickle.load(open(cache, 'rb'))
else:
    j = json.load(open(sj))
    meth = {m['Address']: m['Name'] for m in j['ScriptMethod']}
    meta = {}
    for k in ('ScriptMetadata', 'ScriptMetadataMethod'):
        for m in j.get(k, []): meta[m['Address']] = m['Name']
    for m in j.get('ScriptString', []): meta[m['Address']] = 'str:"%s"' % m['Value'][:60]
    pickle.dump((meth, meta), open(cache, 'wb'))
byname = {v: k for k, v in meth.items()}
addrs = sorted(meth)
data = open(so, 'rb').read()
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
import bisect
for name in names:
    if name not in byname:
        print('?? not found', name, [n for n in byname if name.split('$$')[-1] in n and name.split('$$')[0] in n][:5]); continue
    a = byname[name]; end = addrs[bisect.bisect_right(addrs, a)]
    end = min(end, a + 4 * 600)
    print(f'===== {name} @ {a:#x} ({(end-a)//4} insns)')
    regs = {}
    for ins in md.disasm(data[a:end], a):
        note = ''
        if ins.mnemonic == 'adrp':
            r, imm = ins.op_str.split(', ')
            regs[r] = int(imm.replace('#', ''), 16)
        elif ins.mnemonic in ('ldr', 'add') and '#' in ins.op_str:
            m = re.match(r'(\w+), \[?(\w+)(?:, #(-?0x[0-9a-f]+|-?\d+))?\]?', ins.op_str)
            if m and m.group(2) in regs:
                tgt = regs[m.group(2)] + int(m.group(3) or '0', 0)
                if tgt in meta: note = '  ; ' + meta[tgt]
                elif tgt in meth: note = '  ; &' + meth[tgt]
        if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#'):
            t = int(ins.op_str[1:], 16)
            if t in meth: note = '  ; ' + meth[t]
        print(f'  {ins.address:#x}: {ins.mnemonic:6s} {ins.op_str}{note}')
        if ins.mnemonic == 'ret' and ins.address > a + 8:
            pass
