"""forgelib/crossmaps/acb_configs.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import configs as port_configs
from acb_calls import MERCHANT_CALLS, ORATOR_SPEECHES
AC2_TEX1_PIXELLIT = '0x728548D5'
ACFE_TEX1_PIXELLIT = '0x17AB0B95'
TEMPLATE_DONOR = 'DataPC_AC3MP_Boston_Brewer.forge'
ACR_TEMPLATES = 'DataPC_ACFE_Constantinople.forge'

def acb_chain(root, world, layout, bank, oob, uid_base, sound=True, tod_from_souk=False, sky=None, evening_souk=False, sky_at=(), calls=None, orators=True, tree_prefixes=(), aa=None):
    stage1 = [['port/dlc_world.py', '{A}', '{M}'], ['archive_fixes.py', '{A}'], ['layer_links.py', '{A}'], ['shaders.py', '{A}'], ['training_block.py', '{A}']]
    if oob:
        stage1.append(['oob_block.py', '{A}', '--cell', oob[0], '--entity', oob[1]])
    _s1, galata2 = port_configs.acfe_chain(root, world, layout, bank)
    rename = {'ACFE_BUC_GEN_Rope_': 'AC2_GEN_Rope_', 'ACFE_BUC_GEN_RT_': 'AC2_GEN_RT_'}
    stage2 = []
    for step in galata2:
        if step[0] == 'port/fadein_materials.py':
            continue
        step = [rename.get(s, s) for s in step]
        if step[0] == 'lift_dropped.py' and '--crowdlife' in step:
            step = ['lift_dropped.py', '{A}', '{M}', '--entities', 'AC2MP_GEN_Static_Group', '--crowdlife']
        if step[0] == 'swap_template.py':
            step = ['swap_template.py', '{A}', '--map', ACR_TEMPLATES]
        if step[0] == 'chase_breaker_sounds.py' and (not sound):
            continue
        if step[0] == 'lift_dropped.py' and step[-2:] == ['--entities', 'AC2_GEN_RT_']:
            stage2.append(step)
            for tp in tree_prefixes:
                stage2.append(['lift_dropped.py', '{A}', '{M}', '--entities', tp])
            continue
        if step[0] == 'realtree_convert.py':
            stage2.append(step)
            stage2.append(['tree_material_users.py', '{A}'])
            continue
        if step[0] == 'port/root_closure.py':
            stage2.append(['port/mode_spawns.py', '{A}', '--cells'])
        stage2.append(step)
        if step[0] == 'acr_templates.py' and '--from-substitutes' in step:
            stage2.append(['world_materials.py', '{A}'])
        if step[0] == 'pack_assets.py':
            stage2.append(['import_ac3.py', '{A}', '--from', TEMPLATE_DONOR, '--root', ACFE_TEX1_PIXELLIT, '--into', root])
    if tod_from_souk:
        stage2 += [['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '20'], ['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '0', '--also', '24D32CF8/1EEEC2CF', '--also', '24D32CF8/16E80C76', '--also', '24D32CF8/E073D400']]
    if evening_souk:
        stage2.append(['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '20'])
        for at in sky_at:
            stage2.append(['port/sky_color.py', '{A}', '--at', at])
    if sky:
        stage2.append(['../crossmaps/acb_sky.py', '{A}', '--texture', sky['texture'], '--night-from', sky['night_from'], '--evening', sky['evening']] + (['--max-error', str(sky['max_error'])] if sky.get('max_error') else []))
    stage2 += [['lift_dropped.py', '{A}', '{M}', '--entities', 'AC2MP_GEN_AMerchant', '--crowdlife'], ['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{uid_base + 49152:#010x}', '--present', 'root', '--stalls', 'AC2MP_GEN_AMerchant'], ['../npc_port.py', '{A}', '--into', root, '--uid-base', f'{uid_base + 16384:#010x}', '--uid-limit', '0x4000', '--available', 'root', '--stalls', 'AC2MP_GEN_AMerchant']]
    if sound:
        stage2.append(['../crossmaps/acb_calls.py', '{A}', '--uid-base', f'{uid_base + 57344:#010x}', '--stalls', 'AC2MP_GEN_AMerchant', '--calls', (calls or (MERCHANT_CALLS, ORATOR_SPEECHES))[0]])
    if not orators:
        return (stage1, stage2 + aa_steps(root, uid_base, aa) + oob_steps(root))
    stage2 += [['lift_dropped.py', '{A}', '{M}', '--entities', 'AC2MP_GEN_AOrator', '--crowdlife'], ['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{uid_base + 40960:#010x}', '--uid-limit', '0x700', '--present', 'root', '--stalls', 'AC2MP_GEN_AOrator'], ['../npc_port.py', '{A}', '--into', root, '--uid-base', f'{uid_base + 43008:#010x}', '--uid-limit', '0x1800', '--available', 'root', '--npc', 'herald', '--stalls', 'AC2MP_GEN_AOrator'], ['port/orator_speech.py', '{A}', '--into', root, '--uid-base', f'{uid_base + 42752:#010x}']]
    if sound:
        stage2.append(['../crossmaps/acb_calls.py', '{A}', '--uid-base', f'{uid_base + 42880:#010x}', '--uid-limit', '0x80', '--stalls', 'AC2MP_GEN_AOrator', '--calls', (calls or (MERCHANT_CALLS, ORATOR_SPEECHES))[1]])
    return (stage1, stage2 + aa_steps(root, uid_base, aa) + oob_steps(root))

def oob_steps(root):
    return [['port/oob_modes.py', '{A}', '--library', root]]
AA_BLOCK = 'DataBlock_ACFE_CTF_2'

def aa_steps(root, uid_base, aa):
    aa = aa or {}
    zones = ['../crossmaps/acb_aa_zones.py', '{A}', '--cells', '--into', AA_BLOCK, '--uid-base', f'{uid_base + 37120:#010x}']
    if aa.get('play_box'):
        zones += ['--play-box', aa['play_box']]
    if aa.get('area_box'):
        zones += [f"--area-box={aa['area_box']}"]
    ctf = ['../crossmaps/acb_ctf.py', '{A}', '--into', AA_BLOCK, '--uid-base', f'{uid_base + 36864:#010x}']
    if aa.get('play_box'):
        ctf.append(f"--play-box={aa['play_box']}")
    if aa.get('area_box'):
        ctf.append(f"--area-box={aa['area_box']}")
    return [ctf, zones]
MERCHANT_OVERRIDES = {('npc_port', 'SELECTOR'): 184853401, ('npc_port', 'BODY_A'): 184853340, ('npc_port', 'BODY_B'): 184854534, ('npc_port', 'HERALD_HEADS'): 184854916, ('orator_speech', 'PREFIX'): 'AC2MP_GEN_AOrator'}
MERCHANT_OVERRIDES_ALHAMBRA = {('npc_port', 'SELECTOR'): 1531356977, ('npc_port', 'BODY_A'): 1531356999, ('npc_port', 'BODY_B'): 1531357017, ('npc_port', 'HERALD_HEADS'): 3348690532, ('orator_speech', 'PREFIX'): 'AC2MP_GEN_AOrator'}
MAPS = {'forli': {'map': 'AC2MP_Forli', 'archive': 'DataPC_AC2MP_Forli.forge', 'world': 'AC2MP_Forli', 'tag': 'forli', 'root': 'Cell00084_DataBlock', 'uid_base': 1527775232, 'oob': ('Cell00019_DataBlock', 'AC2MP_FOR_OOB_03a'), 'sky': {'texture': 'AC2MP_Forli_Skydome_color_Map', 'night_from': 'DataPC_ACFE_Constantinople.forge', 'evening': '190,165,140'}, 'tod_from_souk': True, 'aa': {'play_box': '-58,-51,56,59'}, 'stage0_src': 'BF20s_forli', 'bank': '0x4AF2EC19', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'UID_BASE'): 1527775232, ('map_entry', 'WORLD_NEW'): 1409597301, ('map_entry', 'PICTURES'): {1609142200: 3768433736}, **MERCHANT_OVERRIDES}, 'MENU': {'label': 'AC2MP_Forli', 'name_acb': '0x4B241', 'desc_acb': '0x4B24A', 'name_id': '0x5AE20000', 'desc_id': '0x5AE20001', 'unlock': 2428, 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0xE09DBC48', 'unlock': 2429}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0xE09DBC48', 'unlock': 2430}]}}, 'alhambra': {'map': 'AC2MP_Alhambra_dlc', 'archive': 'DataPC_AC2MP_Alhambra_dlc.forge', 'install_as': 'DataPC_AC2MP_Alhambra.forge', 'world': 'AC2MP_Alhambra', 'tag': 'alh', 'evening_souk': True, 'sky_at': ('16=20',), 'calls': ('0xF32E74DA,0xF32E74D9,0xF32E74D8,0xF32E74DF,0xF32E74DE', '0xA4D13536,0x49566210'), 'root': 'Cell00084_DataBlock', 'uid_base': 1527971840, 'oob': ('Cell00027_DataBlock', 'AC2MP_ALH_OOB_001'), 'bank': '0x6B041403', 'stage0_src': 'AL0s_alh', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'UID_BASE'): 1527971840, ('map_entry', 'WORLD_NEW'): 1119345892, ('map_entry', 'PICTURES'): {1609142200: 1496143864}, **MERCHANT_OVERRIDES_ALHAMBRA}, 'MENU': {'label': 'AC2MP_Alhambra', 'name_acb': '0x4BA9C', 'desc_acb': '0x4BA9D', 'name_id': '0x5AE20002', 'desc_id': '0x5AE20003', 'unlock': 2437, 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x592D57F8', 'unlock': 2438}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x592D57F8', 'unlock': 2439}]}}, 'villa': {'map': 'AC2MP_Villa', 'archive': 'DataPC_AC2MP_Villa.forge', 'world': 'AC2MP_Villa', 'tag': 'villa', 'root': 'Cell00084_DataBlock', 'uid_base': 1528037376, 'oob': None, 'orators': False, 'aa': {'area_box': '-69,-49,72,75'}, 'layout': 'RegionLayout', 'tree_prefixes': ('GEN_RT_',), 'calls': ('0xF22C8CDB,0x413CF78A', ''), 'sky': {'texture': 'AC2MP_Villa_SkyColor_Map', 'night_from': 'DataPC_ACFE_Constantinople.forge', 'evening': '190,165,140', 'max_error': 20}, 'tod_from_souk': True, 'stage0_src': 'VI1s_villa', 'bank': '0xF514F575', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'UID_BASE'): 1528037376, ('map_entry', 'WORLD_NEW'): 359541184, ('map_entry', 'PICTURES'): {1609142200: 3768433744}, **MERCHANT_OVERRIDES}, 'MENU': {'label': 'AC2MP_Villa', 'name_acb': '0x4B240', 'desc_acb': '0x4B249', 'name_id': '0x5AE20004', 'desc_id': '0x5AE20005', 'unlock': 2431, 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0xE09DBC50', 'unlock': 2432}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0xE09DBC50', 'unlock': 2433}]}}, 'pienza': {'map': 'AC2MP_Pienza_dlc', 'archive': 'DataPC_AC2MP_Pienza_dlc.forge', 'install_as': 'DataPC_AC2MP_Pienza.forge', 'world': 'AC2MP_Pienza', 'tag': 'pie', 'evening_souk': True, 'sky_at': ('16=20',), 'root': 'Cell00084_DataBlock', 'uid_base': 1528233984, 'oob': ('Cell00036_DataBlock', 'AC2MP_PIE_OOB_001'), 'orators': False, 'layout': 'CrowdRegions', 'bank': '0xE747F25C', 'stage0_src': 'PI1s_pie', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'UID_BASE'): 1528233984, ('map_entry', 'WORLD_NEW'): 271347493, ('map_entry', 'PICTURES'): {1609142200: 1316564396}, **MERCHANT_OVERRIDES}, 'MENU': {'label': 'AC2MP_Pienza', 'name_acb': '0x4B23E', 'desc_acb': '0x4B247', 'name_id': '0x5AE20006', 'desc_id': '0x5AE20007', 'unlock': 2434, 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x4E792DAC', 'unlock': 2435}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x4E792DAC', 'unlock': 2436}]}}}
for _cfg in MAPS.values():
    _cfg['STAGE1'], _cfg['STAGE2'] = acb_chain(_cfg['root'], _cfg['world'], _cfg.get('layout', 'CrowdRegion'), _cfg.get('bank', '0x0'), _cfg['oob'], _cfg['uid_base'], _cfg.get('sound', True), _cfg.get('tod_from_souk', False), _cfg.get('sky'), _cfg.get('evening_souk', False), _cfg.get('sky_at', ()), _cfg.get('calls'), _cfg.get('orators', True), _cfg.get('tree_prefixes', ()), _cfg.get('aa'))
for _cfg in MAPS.values():
    _cfg.setdefault('OVERRIDES', {}).setdefault(('convert', 'MIN_TARGET'), 65536)
port_configs.with_podium(MAPS)
