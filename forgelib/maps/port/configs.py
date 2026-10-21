"""forgelib/maps/port/configs.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
BARK_DONOR, LEAF_DONOR = ('0xCDC0B7D7', '0xD1AC07C4')

def acfe_chain(root, world, layout, bank, aa_uid_base=None):
    stage1 = [['port/dlc_world.py', '{A}', '{M}'], ['archive_fixes.py', '{A}'], ['shaders.py', '{A}'], ['port/mode_spawns.py', '{A}'], ['port/aa_zones.py', '{A}'] + (['--uid-base', aa_uid_base] if aa_uid_base else [])]
    stage2 = [['library_cell.py', '{A}', '--library', root], ['pack_assets.py', '{A}', '--into', root], ['lift_dropped.py', '{A}', '{M}', '--proven', 'Ambient3dSoundComponent', '--proven', 'SoundActor'], ['lift_dropped.py', '{A}', '{M}', '--entities', 'ACFE_BUC_GEN_Rope_', '--proven', 'Ambient3dSoundComponent'], ['lift_dropped.py', '{A}', '{M}', '--entities', 'ACFE_BUC_GEN_RT_'], ['port/merged_collision.py', '{A}', '{M}'], ['lift_dropped.py', '{A}', '{M}', '--entities', 'ACFE_GEN_Static_Group', '--entities', 'ACFE_GEN_Merchant', '--crowdlife'], ['import_ac3.py', '{A}', '--from', 'DataPC_AC3MP_Boston_Mill.forge', '--into', root], ['lift_dropped.py', '{A}', '{M}', '--entities', '', '--kind', 'CrowdDutyRegion'], ['crowd_region.py', '{A}', '{M}', '--world-file', world, '--layout', layout], ['library_cell.py', '{A}', '--library', root], ['import_templates.py', '{A}', '{M}', '--world', world, '--era', 'acr'], ['swap_template.py', '{A}', '{M}'], ['acr_templates.py', '{A}', '{M}', '--world', world, '--into', root], ['acr_templates.py', '{A}', '{M}', '--world', world, '--into', root, '--from-substitutes'], ['tex_group.py', '{A}', '--from', '11', '--to', '0'], ['lod_scale.py', '{A}', '--all', '--scale', '2'], ['mute_bank.py', '{A}', '--bank', bank], ['chase_breaker_sounds.py', '{A}'], ['import_ac3.py', '{A}', '--from', 'DataPC_AC2MP_Whiteroom.forge', '--root', '0x70FA9660', '--into', root], ['tod_snow.py', '{A}'], ['import_ac3.py', '{A}', '--from', 'DataPC_AC3MP_Boston_Mill.forge', '--root', BARK_DONOR, '--root', LEAF_DONOR, '--into', root], ['realtree_convert.py', '{A}', '--bark-donor', BARK_DONOR, '--leaf-donor', LEAF_DONOR, '--tint', 'acr', '--into', root], ['port/fadein_materials.py', '{A}'], ['port/tree_stubs.py', '{A}', '{M}'], ['port/root_closure.py', '{A}', '--library', root], ['port/stock_twins.py', '{A}']]
    return (stage1, stage2)

def merchant_steps(root, block):
    return [['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{block + 49152:#010x}', '--present', 'root'], ['../npc_port.py', '{A}', '--into', root, '--uid-base', f'{block + 16384:#010x}', '--uid-limit', '0x4000', '--available', 'root'], ['port/merchant_calls.py', '{A}', '--uid-base', f'{block + 57344:#010x}']]
MILL, WHITEROOM = ('DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC2MP_Whiteroom.forge')

def souk_chain(map_name, world, root, layers, oob_cell, oob_entity, dy, bank, filler_match, filler_regex, initial_moves, aa_uid_base, npc_steps, dx='0', filler_depths=(), crowdlife=(), library_after_regrid=False, skip_classes=(), skip_entities=(), fadein_materials=True):
    east = ['--dx', dx] if dx not in ('0', 0) else []
    build_static = [map_name, '--current', '--proven', 'Ambient3dSoundComponent', '--proven', 'SoundActor', '--keep-trees', '--crowdlife', 'ACFE_GEN_Static_Group', '--crowdlife', 'ACFE_GEN_Merchant'] + [x for c in crowdlife for x in ('--crowdlife', c)] + ['--bank', bank] + [x for c in skip_classes for x in ('--skip-class', c)] + [x for e in skip_entities for x in ('--skip-entity', e)]
    stage1 = [['archive_fixes.py', '{A}'], ['shaders.py', '{A}'], ['spawn_layers.py', '{A}', '--map', layers, '--initial-for'], ['training_block.py', '{A}'], ['layer_tails.py', '{A}', '--map', layers], ['oob_block.py', '{A}', '--cell', oob_cell, '--entity', oob_entity]]
    stage2 = [['library_cell.py', '{A}', '--library', root], ['pack_assets.py', '{A}', '--into', root], ['import_ac3.py', '{A}', '--from', MILL, '--into', root], ['shift_map.py', '{A}', '--dy', dy] + east, ['regrid.py', '{A}', '--dy', dy] + east] + ([['library_cell.py', '{A}', '--library', root, '--off-grid']] if library_after_regrid else []) + [['fake_tiles.py', '{A}'], ['leaf_index.py', '{A}'], ['nav_cells.py', '{A}', '--dy', dy] + east, ['spawn_settings.py', '{A}', '--dy', dy] + east, ['floor_heights.py', '{A}', '--dy', dy] + east, ['fake_mesh_leaf.py', '{A}'], ['import_templates.py', '{A}', '{M}', '--world', world, '--era', 'acr'], ['swap_template.py', '{A}', '{M}'], ['acr_templates.py', '{A}', '{M}', '--world', world, '--into', root], ['acr_templates.py', '{A}', '{M}', '--world', world, '--into', root, '--from-substitutes'], ['tex_group.py', '{A}', '--from', '11', '--to', '0'], ['lod_scale.py', '{A}', '--all', '--scale', '2'], ['import_ac3.py', '{A}', '--from', WHITEROOM, '--root', '0x70FA9660', '--into', root], ['filler_clip.py', '{A}'] + [x for m in filler_match for x in ('--match', m)], ['filler_dedupe.py', '{A}', '--fillers', filler_regex], ['filler_offset.py', '{A}', '--fillers', filler_regex] + [x for d in filler_depths for x in ('--offset', d)], ['import_ac3.py', '{A}', '--from', MILL, '--root', BARK_DONOR, '--root', LEAF_DONOR, '--into', root], ['realtree_convert.py', '{A}', '--bark-donor', BARK_DONOR, '--leaf-donor', LEAF_DONOR, '--tint', 'acr', '--into', root]] + ([['port/fadein_materials.py', '{A}']] if fadein_materials else []) + [['chase_breaker_sounds.py', '{A}'], ['tod_snow.py', '{A}']] + ([['initial_spawns.py', '{A}'] + [x for m in initial_moves for x in ('--move', m)]] if initial_moves else []) + [['port/mode_spawns.py', '{A}', '--cells'], ['port/root_closure.py', '{A}', '--library', root], ['port/aa_zones.py', '{A}', '--cells', '--into', 'DataBlock_ACFE_CTF_2', '--uid-base', aa_uid_base], ['port/stock_twins.py', '{A}']] + npc_steps + [['port/oob_modes.py', '{A}', '--library', root]]
    return (build_static, stage1, stage2)

def _souk():
    root = 'Cell01364_DataBlock'
    return souk_chain('ACFE_Souk', 'ACFE_Souk', root, 'Souk', 'Cell00400_DataBlock', 'ACFE_RHO_SOU_OOB_01A_001', '128', '0x3CCFDF89', ['AFCE_RHO_Matte_01A_002', 'ACFE_Rhodes_Souk_FakeMesh_13_001', 'ACFE_Rhodes_Souk_FakeMesh_15_001', 'ACFE_Rhodes_Souk_FakeMesh_26_001'], '^(ACFE_Rhodes_Souk_FakeMesh_\\d+_001|ACFE_RHODES_Souk_FakeMesh_\\d+_001|ACFE_RHO_Fakemesh_KnightHospital_001|AFCE_RHO_Matte_01A_002)$', ['SpawnPoint_Initial_Wanted_004=SpawnPoint_Wanted_008', 'SpawnPoint_Initial_Wanted_007=SpawnPoint_Wanted_004', 'SpawnPoint_Initial_Wanted_008=SpawnPoint_Wanted_003'], '0x5AD40000', [['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', '0x5AD4C000'], ['../npc_port.py', '{A}', '--into', root, '--uid-base', '0x5ADA0000'], ['port/merchant_calls.py', '{A}', '--uid-base', '0x5AD4E000']])
ORATOR_SPEECHES = '0x91104B58,0x41320725,0xF25C280D,0x3DE24760'

def orator_steps(root, block, lift=True):
    return ([['lift_dropped.py', '{A}', '{M}', '--entities', 'ACFE_GEN_Orator', '--crowdlife']] if lift else []) + [['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{block + 40960:#010x}', '--uid-limit', '0x800', '--present', 'root', '--stalls', 'ACFE_GEN_Orator'], ['../npc_port.py', '{A}', '--into', root, '--uid-base', f'{block + 43008:#010x}', '--uid-limit', '0x1800', '--available', 'root', '--npc', 'herald', '--stalls', 'ACFE_GEN_Orator'], ['port/orator_speech.py', '{A}', '--into', root, '--uid-base', f'{block + 42752:#010x}'], ['port/merchant_calls.py', '{A}', '--uid-base', f'{block + 42880:#010x}', '--uid-limit', '0x80', '--stalls', 'ACFE_GEN_Orator', '--calls', ORATOR_SPEECHES]]

def _galata():
    stage1, stage2 = acfe_chain('Cell00084_DataBlock', 'ACFE_Constantinople', 'CrowdRegions', '0x4EDB578E')
    stage2 += [['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '20']]
    stage2 += merchant_steps('Cell00084_DataBlock', 1523712000) + orator_steps('Cell00084_DataBlock', 1523712000)
    return (stage1, stage2)
MAPS = {'constantinople': {'map': 'ACFE_Constantinople', 'archive': 'DataPC_ACFE_Constantinople.forge', 'world': 'ACFE_Constantinople', 'tag': 'const', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'WORLD_NEW'): 1425145005, ('map_entry', 'PICTURES'): {1609142200: 179766381}, ('map_entry', 'UID_BASE'): 1523712000, ('realtree_convert', 'SELECTORS'): 'acr', ('realtree_convert', 'CARD_LIFT'): True}, 'STAGE1': _galata()[0], 'STAGE2': _galata()[1], 'MENU': {'label': 'ACFE_Constantinople', 'name_id': '0x51BFC', 'desc_id': '0x5AE00002', 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x58A16F2D'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x0AB7046D'}], 'desc_text': "Steep streets, markets and rooftops above the Golden Horn. Ported from Assassin's Creed Revelations."}}, 'antioch': {'map': 'ACFE_Antioch', 'archive': 'DataPC_ACFE_Antioch.forge', 'world': 'ACFE_Antioch', 'tag': 'antioch', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'WORLD_NEW'): 1668052579, ('map_entry', 'PICTURES'): {1609142200: 179766371}, ('map_entry', 'UID_BASE'): 1523908608}, 'STAGE1': acfe_chain('Cell00084_DataBlock', 'ACFE_Antioch', 'RegionLayout', '0x59BB8683', '0x5AD58000')[0], 'STAGE2': acfe_chain('Cell00084_DataBlock', 'ACFE_Antioch', 'RegionLayout', '0x59BB8683', '0x5AD58000')[1] + [['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '20']] + merchant_steps('Cell00084_DataBlock', 1523908608) + orator_steps('Cell00084_DataBlock', 1523908608), 'MENU': {'label': 'ACFE_Antioch', 'name_id': '0x5AE00004', 'name_acr': '0x51BFA', 'desc_id': '0x5AE00003', 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x58A16F23'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x0AB70463'}], 'desc_text': "Rooftops, stables and a mosque courtyard in the crusader city on the Orontes. Ported from Assassin's Creed Revelations."}}, 'jerusalem': {'map': 'ACFE_Jerusalem_dlc', 'archive': 'DataPC_ACFE_Jerusalem_dlc.forge', 'install_as': 'DataPC_ACFE_Jerusalem.forge', 'world': 'ACFE_Jerusalem', 'tag': 'jeru', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'WORLD_NEW'): 1690371384, ('map_entry', 'PICTURES'): {1609142200: 1834802219}, ('map_entry', 'UID_BASE'): 1523974144}, 'STAGE1': acfe_chain('Cell00084_DataBlock', 'ACFE_Jerusalem', 'CrowdRegions', '0x11930F83', '0x5AD68000')[0], 'STAGE2': acfe_chain('Cell00084_DataBlock', 'ACFE_Jerusalem', 'CrowdRegions', '0x11930F83', '0x5AD68000')[1] + [['port/tod_retime.py', '{A}', '--at', '18=12', '--at', '8=20'], ['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Constantinople.forge', '--hour', '0', '--also', '24D32CF8/1EEEC2CF', '--also', '24D32CF8/16E80C76', '--also', '24D32CF8/E073D400', '--mix', '0.75'], ['port/sky_color.py', '{A}', '--at', '8=20', '--night-from', 'DataPC_ACFE_Constantinople.forge']] + merchant_steps('Cell00084_DataBlock', 1523974144) + orator_steps('Cell00084_DataBlock', 1523974144) + [['lift_dropped.py', '{A}', '{M}', '--entities', 'ACFE_BUC_GEN_GFX_Water_Splash', '--proven', 'Ambient3dSoundComponent'], ['library_cell.py', '{A}', '--library', 'Cell00084_DataBlock']], 'MENU': {'label': 'ACFE_Jerusalem', 'name_id': '0x5AE00006', 'name_acr': '0x51BFB', 'desc_id': '0x5AE00005', 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x6D5CDC2B'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x6D5CDC35'}], 'desc_text': "Pilgrims' streets, domes and rooftops of the Holy City. Ported from Assassin's Creed Revelations."}}, 'dyers': {'map': 'ACFE_Dyers_dlc', 'archive': 'DataPC_ACFE_Dyers_dlc.forge', 'install_as': 'DataPC_ACFE_Dyers.forge', 'world': 'ACFE_Dyers', 'tag': 'dyers', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'WORLD_NEW'): 81307707, ('map_entry', 'PICTURES'): {1609142200: 1680187651}, ('map_entry', 'UID_BASE'): 1524039680}, 'STAGE1': acfe_chain('Cell00084_DataBlock', 'ACFE_Dyers', 'Dyers_CrowdRegions', '0x8772C33E', '0x5AD78000')[0], 'STAGE2': acfe_chain('Cell00084_DataBlock', 'ACFE_Dyers', 'Dyers_CrowdRegions', '0x8772C33E', '0x5AD78000')[1] + [['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '20'], ['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '0', '--also', '24D32CF8/1EEEC2CF', '--also', '24D32CF8/16E80C76', '--also', '24D32CF8/E073D400']] + merchant_steps('Cell00084_DataBlock', 1524039680), 'MENU': {'label': 'ACFE_Dyers', 'name_id': '0x5AE00008', 'name_acr': '0x52AA7', 'desc_id': '0x5AE00007', 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x6425A103'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x6425A0F5'}], 'desc_text': "Dye vats, stone courtyards and the rooftops of the dyers' quarter. Ported from Assassin's Creed Revelations."}}, 'imperial': {'map': 'ACFE_Imperial_dlc', 'archive': 'DataPC_ACFE_Imperial_dlc.forge', 'install_as': 'DataPC_ACFE_Imperial.forge', 'world': 'ACFE_Imperial', 'tag': 'imperial', 'OVERRIDES': {('archive_fixes', 'EMPTY_TEMPLATE'): 'DataBlock_z_AC2_LGS01', ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'WORLD_NEW'): 535646445, ('map_entry', 'PICTURES'): {1609142200: 1680187609}, ('map_entry', 'UID_BASE'): 1524105216}, 'STAGE1': acfe_chain('Cell00084_DataBlock', 'ACFE_Imperial', 'Imperial_Crowd', '0xC19CAB78', '0x5AD88000')[0], 'STAGE2': acfe_chain('Cell00084_DataBlock', 'ACFE_Imperial', 'Imperial_Crowd', '0xC19CAB78', '0x5AD88000')[1] + [['port/tod_retime.py', '{A}', '--at', '12=12', '--at', '17=20'], ['port/sky_color.py', '{A}', '--night-from', 'DataPC_ACFE_Constantinople.forge']] + merchant_steps('Cell00084_DataBlock', 1524105216) + orator_steps('Cell00084_DataBlock', 1524105216), 'MENU': {'label': 'ACFE_Imperial', 'name_id': '0x5AE0000A', 'name_acr': '0x52AA5', 'desc_id': '0x5AE00009', 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x6425A0D9'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x6425A0E7'}], 'desc_text': "Palms, arcades and the domes of the imperial quarter. Ported from Assassin's Creed Revelations."}}}
_SOUK = _souk()
MAPS['souk'] = {'map': 'ACFE_Souk', 'archive': 'DataPC_ACFE_Souk.forge', 'world': 'ACFE_Souk', 'tag': 'souk', 'OVERRIDES': {}, 'BUILD_STATIC': _SOUK[0], 'STAGE1': _SOUK[1], 'STAGE2': _SOUK[2]}

def _juderia():
    root = 'Cell01364_DataBlock'
    block = 1524170752
    return souk_chain('ACFE_Juderia', 'ACFE_Juderia', root, 'Juderia', 'Cell00564_DataBlock', 'ACFE_JUD_OOB_002', '0', '0xBF253A0B', ['ACFE_RHO_JUD_matte01A_001', 'ACFE_RHO_JUD_matte02A_001'], '^(ACFE_Rhodes_Juderia_FakeMesh_\\d+_001|ACFE_Rhodes_FakeMesh_\\d+_002|ACFE_ACFE_RHO_Fakemesh_Souk_01A_001|ACFE_RHO_Juderia_Fakemesh_KnightHospital_001|ACFE_RHO_JUD_matte0[12]A_001)$', ['SpawnPoint_Initial_Wanted_004=SpawnPoint_Wanted_003', 'SpawnPoint_Initial_Wanted_007=SpawnPoint_Wanted_004', 'SpawnPoint_Initial_Wanted_008=SpawnPoint_Wanted_005'], f'{block + 32768:#010x}', merchant_steps(root, block) + orator_steps(root, block, lift=False), dx='-128', filler_depths=('ACFE_RHO_JUD_matte01A_001=4', 'ACFE_RHO_JUD_matte02A_001=4'), crowdlife=('ACFE_GEN_Orator',), library_after_regrid=True)
_JUD = _juderia()
MAPS['juderia'] = {'map': 'ACFE_Juderia', 'archive': 'DataPC_ACFE_Juderia.forge', 'world': 'ACFE_Juderia', 'tag': 'jud', 'OVERRIDES': {('map_entry', 'WORLD_NEW'): 3162064615, ('map_entry', 'PICTURES'): {1609142200: 1486974785}, ('map_entry', 'UID_BASE'): 1524170752}, 'BUILD_STATIC': _JUD[0], 'STAGE1': _JUD[1], 'STAGE2': _JUD[2], 'MENU': {'label': 'ACFE_Juderia', 'name_id': '0x5AE0000C', 'name_acr': '0x51BFD', 'desc_id': '0x5AE0000B', 'unlock': [2471, 2472, 2473], 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x58A16F4B'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x58A16F41'}], 'desc_text': "The squares, stairs and rooftops round the Hippocrates fountain in the old town of Rhodes. Ported from Assassin's Creed Revelations."}}

def _rhodes():
    root = 'Cell01364_DataBlock'
    block = 1524301824
    return souk_chain('ACFE_Rhodes', 'ACFE_Rhodes', root, 'Hospital', root, 'ACFE_RHO_HOS_OOB_02A_001', '-128', '0xCC7D32C4', ['ACFE_RHO_ground_fakeCity_01A_001'], '^(ACFE_Rhodes_KNI_FakeMesh_\\d+_001|ACFE_ACFE_RHO_Fakemesh_Souk_01A_001|ACFE_RHO_KNI_Fakemesh_Mosque_01A_001|ACFE_RHO_Ground_NorthWest_Fake_0[12]A_001|ACFE_RHO_ground_fakeCity_01A_001)$', ['SpawnPoint_Initial_Wanted_004=SpawnPoint_Wanted_003', 'SpawnPoint_Initial_Wanted_007=SpawnPoint_Wanted_004', 'SpawnPoint_Initial_Wanted_008=SpawnPoint_Wanted_008'], f'{block + 32768:#010x}', merchant_steps(root, block) + orator_steps(root, block, lift=False), crowdlife=('ACFE_GEN_Orator',), library_after_regrid=True, skip_classes=('MPTip',), skip_entities=('ACFE_RHO_fake_forest',), fadein_materials=True)
_RHO = _rhodes()
MAPS['rhodes'] = {'map': 'ACFE_Rhodes', 'archive': 'DataPC_ACFE_Rhodes.forge', 'world': 'ACFE_Rhodes', 'tag': 'rho', 'OVERRIDES': {('map_entry', 'WORLD_NEW'): 397083892, ('map_entry', 'PICTURES'): {1609142200: 179766391}, ('map_entry', 'UID_BASE'): 1524301824}, 'BUILD_STATIC': _RHO[0], 'STAGE1': _RHO[1], 'STAGE2': _RHO[2], 'MENU': {'label': 'ACFE_Rhodes', 'name_id': '0x5AE0000D', 'name_acr': '0x51B8B', 'desc_id': '0x5AE0000E', 'unlock': [2478, 2479, 2480], 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': '0x58A16F55'}, {'mode': 'Evening', 'hour': 20.0, 'picture': '0x0AB70477'}], 'desc_text': "The courtyards, ramparts and rooftops round the Knights Hospital in the old town of Rhodes. Ported from Assassin's Creed Revelations."}}

def ac2mp_chain(root, world, layout, bank, aa_uid_base=None, trees=True, drop=(), oob_from=None, area_box=None):
    stage1, galata2 = acfe_chain(root, world, layout, bank, aa_uid_base)
    if oob_from:
        stage1 = [st + ['--oob-from', oob_from] if st[0] == 'port/aa_zones.py' else st for st in stage1]
    if area_box:
        stage1 = [st + [f'--area-box={area_box}'] if st[0] == 'port/aa_zones.py' else st for st in stage1]
    rename = {'ACFE_BUC_GEN_Rope_': 'AC2_GEN_Rope_', 'ACFE_BUC_GEN_RT_': 'AC2_GEN_RT_'}
    stage2 = []
    for step in galata2:
        step = [rename.get(s, s) for s in step]
        if not trees and ('AC2_GEN_RT_' in step or step[0] == 'realtree_convert.py' or BARK_DONOR in step):
            continue
        if step[0] == 'port/fadein_materials.py':
            continue
        if step[0] == 'lift_dropped.py' and '--crowdlife' in step:
            step = ['lift_dropped.py', '{A}', '{M}', '--entities', 'AC2MP_GEN_Static_Group', '--crowdlife']
        if step[0] == 'swap_template.py':
            step = ['swap_template.py', '{A}', '--map', 'DataPC_ACFE_Constantinople.forge']
        stage2.append(step)
        if step[0] == 'pack_assets.py':
            stage2.append(['import_ac3.py', '{A}', '--from', 'DataPC_AC3MP_Boston_Brewer.forge', '--root', '0x17AB0B95', '--into', root])
        if step[0] == 'acr_templates.py' and '--from-substitutes' in step:
            stage2.append(['world_materials.py', '{A}'])
    if drop:
        last = max((i for i, st in enumerate(stage2) if st[0] == 'lift_dropped.py'))
        stage2[last + 1:last + 1] = [['drop_entities.py', '{A}', '--groups'] + [x for d in drop for x in ('--match', d)]]
    return (stage1, stage2)
PERFORMER_BUILDERS = (778353939, 3019901615, 3019901606)
PERFORMER_PINS = {778353939: ['CV_StreetPerformer_Colors=2']}

def _into(root):
    return [] if root == 'Cell00084_DataBlock' else ['--into', root]

def ac2mp_performer_steps(root, block, entities, packs=(), carry=(), pins=None, builders=None, stalls='AC2MP_CrowdLife_'):
    use = PERFORMER_BUILDERS if builders is None else [int(b, 0) if isinstance(b, str) else b for b in builders]
    new = [f'0x{b:08X}' for b in use if b not in PERFORMER_BUILDERS]
    if new:
        raise SystemExit(f'performer builder(s) {new} have no uid sub-block: add them to configs.PERFORMER_BUILDERS')
    npc = ['../npc_port.py', '{A}', '--into', root, '--uid-limit', '0x1000', '--available', 'root', '--stalls', stalls, '--npc', 'performer']
    return [['port/performers.py', '{A}', '{M}'] + [x for e in entities for x in ('--entities', e)] + ['--strip', 'WeaponComponent'] + [x for p in packs for x in ('--packs', p)] + [x for c in carry for x in ('--carry', c)] + _into(root)] + [npc + ['--uid-base', f'{block + 32768 + 4096 * k:#x}', '--acr-builder', f'0x{b:08X}'] + [x for pin in (pins or {}).get(b, ()) for x in ('--pin-row', pin)] for k, b in enumerate(PERFORMER_BUILDERS) if b in use]
AC2MP_MERCHANT_CALLS = '0xAC82EB57,0xAC82EB54,0xAD82ECC1,0xAC82EB5A,0xAC82EB5B,0xAD82ECC5,0xAD82ECC6'
AC2MP_ORATOR_SPEECHES = '0x9CF0DA06,0xA4563F1B,0xE4768B68,0x34188D45'

def ac2mp_npc_steps(root, block, merchants=True, orators=False, calls=AC2MP_MERCHANT_CALLS, speeches=AC2MP_ORATOR_SPEECHES, orator_planes=(), orator_names=()):
    out = []
    olift = [x for n in orator_names for x in ('--entities', n)] if orator_names else ['--entities', 'AC2MP_GEN_AOrator']
    ostalls = ','.join(orator_names) if orator_names else 'AC2MP_GEN_AOrator'
    if merchants:
        out += [['lift_dropped.py', '{A}', '{M}', '--entities', 'AC2MP_GEN_AMerchant', '--crowdlife'], ['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{block + 49152:#010x}', '--present', 'root', '--stalls', 'AC2MP_GEN_AMerchant'], ['../npc_port.py', '{A}', '--into', root, '--uid-base', f'{block + 16384:#010x}', '--uid-limit', '0x4000', '--available', 'root', '--stalls', 'AC2MP_GEN_AMerchant'], ['port/merchant_calls.py', '{A}', '--uid-base', f'{block + 57344:#010x}', '--stalls', 'AC2MP_GEN_AMerchant', '--calls', calls]]
    if orators:
        out += [['lift_dropped.py', '{A}', '{M}'] + olift + ['--crowdlife'], ['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{block + 40960:#010x}', '--uid-limit', '0x700', '--present', 'root', '--stalls', ostalls]]
        if orator_planes:
            out.append(['port/orator_plane.py', '{A}', '{M}'] + [x for n in orator_planes for x in ('--orators', n)])
        out += [['../npc_port.py', '{A}', '--into', root, '--uid-base', f'{block + 43008:#010x}', '--uid-limit', '0x1800', '--available', 'root', '--npc', 'herald', '--stalls', ostalls], ['port/orator_speech.py', '{A}', '--into', root, '--uid-base', f'{block + 42752:#010x}'], ['port/merchant_calls.py', '{A}', '--uid-base', f'{block + 42880:#010x}', '--uid-limit', '0x80', '--stalls', ostalls, '--calls', speeches]]
    return out
AC2MP_UID_RANGES = {'merchant model': (16384, 16384), 'orator groups': (40960, 1792), 'orator speech': (42752, 128), 'orator calls': (42880, 128), 'herald': (43008, 6144), 'audiences': (45056, 4096), 'merchant stalls': (49152, 8192), 'merchant calls': (57344, 2048), 'stage 0': (61440, 4096), 'podium': (38912, 1024), 'audiences beside orators': (59392, 2048)}
WALL_READER_ROOTS = ('0xD72AEAA2', '0xD72AEAB7', '0xD72AEACC', '0xD72AEAD8', '0x69F65F8B')
NIGHT_ALSO = ('24D32CF8/1EEEC2CF', '24D32CF8/16E80C76', '24D32CF8/E073D400')
SWAY_TEMPLATE = ('DataPC_AC3MP_Boston_Mill.forge', '0x0FC1FF91', 'AC3MP_Tex1_PixelLit_VertexAnim')

def ac2mp_map(root, world, layout, bank, block, feat, oob_from=None):
    used = []
    if feat.get('merchants'):
        used += ['merchant model', 'merchant stalls', 'merchant calls']
    if feat.get('orators'):
        used += ['orator groups', 'orator speech', 'orator calls', 'herald']
    audiences = 'audiences beside orators' if feat.get('orators') else 'audiences'
    if feat.get('spots'):
        used.append(audiences)
    ranges = [(n, *AC2MP_UID_RANGES[n]) for n in used] + [('stage 0', *AC2MP_UID_RANGES['stage 0'])]
    perf = feat.get('performers')
    if perf:
        b = PERFORMER_BUILDERS if perf.get('builders') is None else [int(x, 0) for x in perf['builders']]
        ranges += [(f'performer model 0x{x:08X}', 32768 + 4096 * k, 4096) for k, x in enumerate(PERFORMER_BUILDERS) if x in b]
        if not (feat.get('merchants') or feat.get('orators')):
            ranges.append(('merchant template', *AC2MP_UID_RANGES['merchant stalls']))
    for i, (n1, s1_, z1) in enumerate(ranges):
        for n2, s2_, z2 in ranges[i + 1:]:
            if s1_ < s2_ + z2 and s2_ < s1_ + z1:
                raise SystemExit(f'uid sub-ranges overlap: {n1} +0x{s1_:04X} and {n2} +0x{s2_:04X} - give one another place')
    s1, s2 = ac2mp_chain(root, world, layout, bank, trees=feat['trees'], drop=tuple(feat.get('drop') or ()), oob_from=oob_from, area_box=feat.get('area_box'))
    if feat.get('tree_prefixes'):
        at = next((i for i, st in enumerate(s2) if st[0] == 'lift_dropped.py' and st[-2:] == ['--entities', 'AC2_GEN_RT_']))
        s2[at + 1:at + 1] = [['lift_dropped.py', '{A}', '{M}', '--entities', tp] for tp in feat['tree_prefixes']]
    if feat.get('fadein'):
        at = next((i for i, st in enumerate(s2) if st[0] == 'realtree_convert.py'))
        s2[at + 1:at + 1] = [['port/fadein_materials.py', '{A}']]
    if feat.get('tree_users'):
        at = next((i for i, st in enumerate(s2) if st[0] == 'realtree_convert.py'))
        s2[at + 1:at + 1] = [['tree_material_users.py', '{A}']]
    tail = []
    if feat.get('spots'):
        tail += [['import_ac3.py', '{A}', '--from', 'DataPC_AC3MP_Boston_Mill.forge'] + [x for r in WALL_READER_ROOTS for x in ('--root', r)] + ['--into', root], ['port/audience_groups.py', '{A}', '{M}'] + [x for sp in feat['spots'] for x in ('--spots', sp)] + ['--marker', 'AC2MP_Merchant_Orator_plane', '--uid-base', f'{block + AC2MP_UID_RANGES[audiences][0]:#x}'] + ([] if audiences == 'audiences' else ['--uid-limit', f'{AC2MP_UID_RANGES[audiences][1]:#x}']), ['drop_entities.py', '{A}'] + [x for pl in feat.get('spot_planes') or ['AC2MP_Merchant_Orator_plane'] for x in ('--match', pl)]]
    tail.append(['port/tod_evening.py', '{A}', '--from', 'DataPC_ACFE_Souk.forge', '--hour', '20'])
    if feat.get('sky_evening'):
        tail.append(['port/sky_evening.py', '{A}', '--texture', feat['sky_evening']])
    if feat.get('night'):
        if not feat.get('sky_evening'):
            raise SystemExit("FEATURES night needs the map's flat sky lookup (sky_evening) for its night columns")
        tail += [['port/tod_evening.py', '{A}', '--from', feat['night'], '--hour', '0'] + [x for q in NIGHT_ALSO for x in ('--also', q)], ['port/sky_evening.py', '{A}', '--texture', feat['sky_evening'], '--hour', '0', '--from', feat['night']]]
    if feat.get('lone'):
        tail += [['lift_dropped.py', '{A}', '{M}'] + [x for e in feat['lone'] for x in ('--entities', e)] + ['--proven', 'Ambient3dSoundComponent'], ['library_cell.py', '{A}', '--library', root]]
    tail += ac2mp_npc_steps(root, block, merchants=bool(feat.get('merchants')), orators=bool(feat.get('orators')), calls=','.join(feat.get('calls') or AC2MP_MERCHANT_CALLS.split(',')), speeches=','.join(feat.get('speeches') or AC2MP_ORATOR_SPEECHES.split(',')), orator_planes=feat.get('orator_planes') or (), orator_names=feat.get('orator_names') or ())
    if perf:
        steps = ac2mp_performer_steps(root, block, perf['entities'], perf.get('packs', ()), perf.get('carry', ()), pins=PERFORMER_PINS, builders=perf.get('builders'))
        if not (feat.get('merchants') or feat.get('orators')):
            steps[1:1] = [['port/merchant_stalls.py', '{A}', '--into', root, '--uid-base', f'{block + 49152:#x}', '--present', 'root', '--stalls', '__no_stall__']]
        tail += steps
    pieces = feat.get('pieces')
    if pieces:
        tail += [['port/performers.py', '{A}', '{M}'] + [x for e in pieces['entities'] for x in ('--entities', e)] + [x for c in pieces.get('proven', ()) for x in ('--proven', c)] + _into(root), ['chase_breaker_sounds.py', '{A}', '--some']]
    if feat.get('grid'):
        tail.append(['port/leaf_fix.py', '{A}'])
    if feat.get('shift'):
        dx, dy = feat['shift']
        aa = next((st for st in s1 if st[0] == 'port/aa_zones.py'))
        s1 = [st for st in s1 if st is not aa]
        tail += [['shift_map.py', '{A}', '--dx', str(dx), '--dy', str(dy), '--placed-only'], ['port/grid_shift.py', '{A}', '--dx', str(dx), '--dy', str(dy)], aa]
    if feat.get('root_layers'):
        tail.append(['port/root_closure.py', '{A}', '--library', root, '--layers'])
    if feat.get('static_materials'):
        tail.append(['port/material_template.py', '{A}'] + [x for m in feat['static_materials'] for x in ('--material', f'{m}=AC2_Tex1_PixelLit')])
    if feat.get('sway_materials'):
        tail += [['import_ac3.py', '{A}', '--from', SWAY_TEMPLATE[0], '--root', SWAY_TEMPLATE[1], '--into', root], ['port/material_template.py', '{A}'] + [x for m in feat['sway_materials'] for x in ('--material', f'{m}={SWAY_TEMPLATE[2]}')]]
    return (s1, s2 + tail)
AC2MP_MERCHANT = (184853401, 184853340, 184854534)
AC2MP_HERALD_HEADS = 184854916

def ac2mp_entry(*, archive, tag, prefix, block, root, world, world_uid, layout, bank, picture, name_id, name_acr, desc_id, desc_text, unlock, features, empty_template='DataBlock_z_AC2_LGS01', merchant=AC2MP_MERCHANT, herald_heads=AC2MP_HERALD_HEADS):
    s1, s2 = ac2mp_map(root, world, layout, bank, block, features)
    map_name = archive[len('DataPC_'):-len('.forge')]
    overrides = {('archive_fixes', 'EMPTY_TEMPLATE'): empty_template, ('library_cell', 'LIBRARY_CELLS'): (), ('map_entry', 'WORLD_NEW'): world_uid, ('map_entry', 'PICTURES'): {1609142200: picture}, ('map_entry', 'UID_BASE'): block, ('npc_port', 'SELECTOR'): merchant[0], ('npc_port', 'BODY_A'): merchant[1], ('npc_port', 'BODY_B'): merchant[2]}
    if features.get('kind_floor'):
        overrides['convert', 'MIN_TARGET'] = features['kind_floor']
    if features.get('orators') or features.get('spots'):
        overrides['npc_port', 'HERALD_HEADS'] = herald_heads
        overrides['orator_speech', 'PREFIX'] = tuple(features['orator_names']) if features.get('orator_names') else 'AC2MP_GEN_AOrator'
    entry = {'map': map_name, 'archive': archive}
    if archive.endswith('_dlc.forge'):
        entry['install_as'] = archive.replace('_dlc.forge', '.forge')
    entry.update({'world': world, 'tag': tag, 'uid_base': block, 'source': f'{prefix}0s_{tag}', 'BUILD_STATIC': [map_name] + [x for c in ('ObjectPack', 'PatrolPath') for x in ('--skip-class', c)] + [x for e in features.get('drop') or () for x in ('--skip-entity', e)], 'OVERRIDES': overrides, 'FEATURES': features, 'STAGE1': s1, 'STAGE2': s2, 'MENU': {'label': world, 'name_id': name_id, 'name_acr': name_acr, 'desc_id': desc_id, 'unlock': list(unlock), 'ambiences': [{'mode': 'Night', 'hour': 0.0, 'picture': f'0x{picture:08X}'}, {'mode': 'Evening', 'hour': 20.0, 'picture': f'0x{picture:08X}'}], 'desc_text': desc_text}})
    return entry
SIENA_FEATURES = {'trees': False, 'tree_prefixes': [], 'fadein': False, 'drop': ['AC2MP_CrowdLife_Lute', 'AC2MP_ENTITY_Cavalry', 'AC2MP_cavalery_patrol', 'GEN_Fiddle'], 'lone': ['GFX_Object_Fire_Torch', 'GFX_FireWork', 'AC2MP_SIE_FireWork', 'GEN_WoodBeam', 'GEN_Pulley', 'AC2MP_SIE_Fireworkentity'], 'spots': [], 'merchants': True, 'orators': False, 'performers': {'entities': ['AC2MP_CrowdLife_FireSpitter', 'AC2MP_CrowdLife_Tambourine', 'AC2MP_CrowdLife_Flutist', 'AC2MP_CrowdLife_Lute Group', 'AC2MP_CrowdLife_Fiddle_Scene', 'GEN_Fiddle_01A'], 'packs': ['Prop_Crowdlife_Tambourine', 'Prop_Crowdlife_Flute', 'Prop_Crowdlife_Lute', 'Prop_Crowdlife_Fiddle', 'Prop_Crowdlife_FiddleBow'], 'carry': ['Crowdlife_FireSpitter', 'Prop_Crowdlife_Torch', 'Prop_Crowdlife_Bottle'], 'builders': ['0x2E64BD13', '0xB4000AAF', '0xB4000AA6']}, 'pieces': None, 'sky_evening': 'AC2MP_Sienna_SkyDome_Color_Map', 'area_box': None}
MAPS['siena'] = ac2mp_entry(archive='DataPC_AC2MP_Siena_dlc.forge', tag='sie', prefix='SI', block=1524432896, root='Cell00084_DataBlock', world='AC2MP_Siena', world_uid=69642372, layout='Crowd_Region', bank='0xF508A8FB', picture=3768433712, name_id='0x5AE0000F', name_acr='0x4B242', desc_id='0x5AE00010', desc_text="Contrade banners, market stalls and rooftops of Siena during the Palio. Ported from Assassin's Creed Revelations.", unlock=[2672, 2673, 2674], features=SIENA_FEATURES)
PALAZZIO_FEATURES = {'trees': True, 'tree_prefixes': [], 'fadein': False, 'drop': ['AC2MP_CrowdLife_', 'GFX_CrowdLife_', 'GEN_CrowdlifeTorch', 'GEN_Fiddle', 'AC2_P02_Chandelier', 'AC2MP_Pal_Chasebreaker_WoodDoor', 'AC2MP_GEN_Pole_chasebreaker_2m'], 'lone': [], 'spots': ['AC2MP_GEN_AMerchant', 'AC2MP_GEN_AOrator'], 'merchants': False, 'orators': False, 'performers': {'entities': ['AC2MP_CrowdLife_FireSpitter', 'AC2MP_CrowdLife_Performer', 'AC2MP_CrowdLife_Fiddle_Scene', 'AC2MP_CrowdLife_Dancer_Rotate', 'AC2MP_CrowdLife_Lute Group'], 'packs': ['Prop_Crowdlife_Lute', 'FX_CrowdLife_FireBreather_Torch', 'FX_CrowdLife_FireBreather', 'FX_CrowdLife_FireBreather_Short', 'GFX_CrowdLife_FireBreather_Torch', 'GFX_CrowdLife_FireBreather'], 'carry': ['Crowdlife_FireSpitter', 'Crowdlife_Performer', 'Crowdlife_Dancer_Rotate', 'Crowdlife_Mu_Fiddle', 'Prop_Crowdlife_Torch', 'Prop_Crowdlife_Bottle', 'Prop_Crowdlife_Fiddle', 'Prop_Crowdlife_FiddleBow'], 'builders': ['0x2E64BD13', '0xB4000AAF', '0xB4000AA6']}, 'pieces': {'entities': ['AC2MP_Pal_Chasebreaker_WoodDoor_Group', 'AC2MP_GEN_Pole_chasebreaker_2m', 'AC2_P02_Chandelier_04AGroup'], 'proven': ['SoundActor']}, 'sky_evening': None, 'area_box': '-62,-48,62,40'}
MAPS['palazzio'] = ac2mp_entry(archive='DataPC_AC2MP_Palazzio.forge', tag='pal', prefix='PA', block=1525022720, root='Cell00084_DataBlock', world='AC2MP_Palazzio', world_uid=601168286, layout='CrowdRegion', bank='0x4D7D6E8B', picture=1486974685, name_id='0x5AE00011', name_acr='0x4B243', desc_id='0x5AE00012', desc_text="Resting in the Alban Hills, Castel Gandolfo is the summer palace of the Pope. Ported from Assassin's Creed Revelations.", unlock=[2694, 2695, 2696], features=PALAZZIO_FEATURES)
FIRENZE_FEATURES = {'trees': True, 'tree_prefixes': ['GEN_RT_Cypres_BIG', 'GEN_RT_Cypres_HUGE', 'GEN_RT_Cypres_MID'], 'fadein': False, 'drop': [], 'lone': ['GEN_Rope'], 'spots': [], 'merchants': True, 'orators': True, 'performers': None, 'pieces': None, 'sky_evening': None, 'area_box': None}
MAPS['firenze'] = ac2mp_entry(archive='DataPC_AC2MP_Firenze_dlc.forge', tag='fir', prefix='FI', block=1525219328, root='Cell00084_DataBlock', world='AC2MP_Firenze', world_uid=555898184, layout='CrowdRegions', bank='0xF2183C1C', picture=3768433728, name_id='0x5AE00013', name_acr='0x4B23B', desc_id='0x5AE00014', desc_text="The marketplace of Florence is at the heart of the city. Traders, citizens and travelers from every walk of life mingle there. Ported from Assassin's Creed Revelations.", unlock=[2747, 2748, 2749], features=FIRENZE_FEATURES)
MTSTMICHEL_FEATURES = {'trees': True, 'tree_prefixes': [], 'fadein': False, 'drop': ['AC2MP_FOR_water_west02_reflector'], 'lone': [], 'spots': [], 'merchants': True, 'orators': True, 'performers': None, 'pieces': None, 'sky_evening': None, 'area_box': None, 'calls': ['0x3342514A', '0x33425149', '0x33425148', '0x3342514F'], 'speeches': ['0x05313C21', '0x3A560A7F', '0x2C04205A', '0x021618E6'], 'orator_planes': ['AC2MP_GEN_AOrator_01a_001'], 'tree_users': True}
MAPS['mtstmichel'] = ac2mp_entry(archive='DataPC_AC2MP_MtStMichel.forge', tag='msm', prefix='MS', block=1525350400, root='Cell00084_DataBlock', world='AC2MP_MtStMichel', world_uid=2876795990, layout='Crowd_Region', bank='0x865F7234', picture=1486974695, name_id='0x5AE00015', name_acr='0x4BAB4', desc_id='0x5AE00016', desc_text="Mont St-Michel is a commune that resides on a rocky tidal island in Normandy, France. Construction of the elaborate church and abbey was begun in the 11th century. Ported from Assassin's Creed Revelations.", unlock=[2791, 2792, 2793], merchant=(1531356973, 1531356990, 1531357008), herald_heads=3348690519, features=MTSTMICHEL_FEATURES)
ROME_FEATURES = {'trees': True, 'tree_prefixes': [], 'fadein': False, 'drop': [], 'lone': [], 'spots': [], 'merchants': True, 'orators': True, 'performers': None, 'pieces': None, 'sky_evening': 'AC2MP_Rome_Skydome_color_Map', 'area_box': None, 'grid': '16 x 16', 'night': 'DataPC_AC2MP_Firenze_dlc.forge', 'tree_users': True, 'kind_floor': 65536}
MAPS['rome'] = ac2mp_entry(archive='DataPC_ACR_Rome_Multi.forge', tag='rom', prefix='RO', block=1525612544, root='Cell00340_DataBlock', world='ACR_Rome_Multi', world_uid=2438306323, layout='CrowdRegions', bank='0xC1584490', picture=1486974715, name_id='0x5AE00017', name_acr='0x4B23D', desc_id='0x5AE00018', desc_text="The Pantheon stands in Rome, commissioned by Marcus Agrippa as a temple to all the gods of the Ancients. Religious processions frequently march past it and the bustling marketplace in front welcomes traders from across Europe. Ported from Assassin's Creed Revelations.", unlock=[3081, 3082, 3083], features=ROME_FEATURES)
SANDONATO_FEATURES = {'trees': True, 'tree_prefixes': ['GEN_RT_Cedre_HUGE', 'GEN_RT_DEAD_HUGE', 'GEN_RT_FAKE_FOREST'], 'fadein': False, 'drop': ['AC2MP_Don_reflector'], 'lone': ['GEN_Boat'], 'spots': [], 'merchants': True, 'orators': True, 'performers': None, 'pieces': None, 'sky_evening': None, 'area_box': None, 'grid': '16 x 16', 'tree_users': True}
MAPS['sandonato'] = ac2mp_entry(archive='DataPC_AC2MP_SanDonato_dlc.forge', tag='sdo', prefix='SD', block=1526071296, root='Cell00340_DataBlock', world='AC2MP_SanDonato', world_uid=2087832802, layout='AC2MP_San_Donato_Crowd', bank='0xEAADD938', picture=3768433720, name_id='0x5AE00019', name_acr='0x4B23C', desc_id='0x5AE0001A', desc_text="San Donato is a peaceful village in the Milanese countryside. Ported from Assassin's Creed Revelations.", unlock=[3207, 3208, 3209], features=SANDONATO_FEATURES)
SANMARCO_FEATURES = {'trees': True, 'tree_prefixes': ['GEN_RT_Bush'], 'fadein': False, 'drop': ['AC2MP_CrowdLife_FireSpitter', 'AC2MP_CrowdLife_Lute Group', 'SMA_Water_Aqua_Reflector'], 'lone': [], 'spots': ['AC2MP_GEN_AOrator_01a_005'], 'spot_planes': ['AC2MP_Merchant_Orator_plane_04_001'], 'merchants': False, 'orators': True, 'orator_names': ['AC2MP_GEN_AOrator_01a_001', 'AC2MP_GEN_AOrator_01a_010'], 'orator_planes': ['AC2MP_GEN_AOrator_01a_001'], 'performers': {'entities': ['AC2MP_CrowdLife_FireSpitter', 'AC2MP_CrowdLife_Lute Group'], 'packs': [], 'carry': ['AC2_GEN_Chair_01A_LOD0', 'Crowdlife_Mu_Lute', 'Prop_Crowdlife_Lute', 'Crowdlife_FireSpitter', 'Prop_Crowdlife_Torch', 'Prop_Crowdlife_Bottle'], 'builders': ['0xB4000AAF', '0x2E64BD13']}, 'pieces': None, 'sky_evening': None, 'area_box': None, 'grid': '16 x 16', 'shift': [-64, 32], 'kind_floor': 65536, 'root_layers': True, 'sway_materials': ['AC2MP_VEN_Cloth_AnimZ_01A', 'AC2MP_VEN_Cloth_AnimZ_Alpha_01A', 'GEN_Flags_01A_Anim01', 'GEN_RopeBeam_Divers_01A_VertexAnim']}
MAPS['sanmarco'] = ac2mp_entry(archive='DataPC_AC2MP_SanMarco.forge', tag='ven', prefix='VE', block=1526267904, root='Cell00340_DataBlock', world='AC2MP_SanMarco', world_uid=1208392919, layout='crowd', bank='0xD0DF47AB', picture=1486974735, name_id='0x5AE0001B', name_acr='0x4B23F', desc_id='0x5AE0001C', desc_text="San Marco is one of the six districts of Venice, lying in the heart of the city. Ported from Assassin's Creed Revelations.", unlock=[3393, 3394, 3395], features=SANMARCO_FEATURES)
KIND_FLOOR_OVERRIDE = ('convert', 'MIN_TARGET')

def with_kind_floor(maps):
    for cfg in maps.values():
        cfg.setdefault('OVERRIDES', {}).setdefault(KIND_FLOOR_OVERRIDE, 65536)
with_kind_floor(MAPS)
CARD_MAPS = ('constantinople', 'antioch', 'jerusalem', 'dyers', 'imperial', 'souk', 'juderia', 'rhodes')

def with_acr_card(maps):
    for key in CARD_MAPS:
        ov = maps[key].setdefault('OVERRIDES', {})
        ov.setdefault(('realtree_convert', 'SELECTORS'), 'acr')
        ov.setdefault(('realtree_convert', 'CARD_LIFT'), True)
with_acr_card(MAPS)
ESCORT_LISTS = os.environ.get('AC3FORGE_ESCORT_LISTS', '1') != '0'
ESCORT_MAPS = ('souk', 'constantinople', 'antioch', 'jerusalem', 'dyers', 'imperial', 'juderia', 'rhodes', 'siena', 'palazzio', 'firenze', 'mtstmichel', 'rome', 'sandonato', 'sanmarco')

def with_escort_lists(maps, on=None):
    if not (ESCORT_LISTS if on is None else on):
        return
    for key in ESCORT_MAPS:
        if key in maps:
            maps[key].setdefault('OVERRIDES', {}).setdefault(('world', 'KEEP_ESCORT_LISTS'), True)
with_escort_lists(MAPS)
MODE_STEPS = os.environ.get('AC3FORGE_MODE_STEPS', '1') != '0'
MODE_STEP_MAPS = ESCORT_MAPS + ('forli', 'alhambra', 'villa', 'pienza')
STEAL_MAPS = ESCORT_MAPS

def mode_steps(key):
    if not MODE_STEPS or key not in MODE_STEP_MAPS:
        return []
    steps = [['../modes/escort_apply.py', '{A}'], ['../modes/chest_apply.py', '{A}'], ['../modes/corruption_apply.py', '{A}']]
    if key in STEAL_MAPS:
        steps.append(['../modes/steal_apply.py', '{A}'])
    return steps
PODIUM = os.environ.get('AC3FORGE_PODIUM', '1') != '0'
PODIUM_AT = {}
PODIUM_BLOCKS = {'souk': 1523843072}

def podium_step(block, at=None):
    step = ['port/podium.py', '{A}', '--uid-base', f"{block + AC2MP_UID_RANGES['podium'][0]:#010x}"]
    for k, v in sorted((at or {}).items()):
        step += [f'--at-{k}', v]
    return step

def with_podium(maps, on=None):
    if not (PODIUM if on is None else on):
        return
    for key, cfg in maps.items():
        block = cfg.get('uid_base') or cfg.get('OVERRIDES', {}).get(('map_entry', 'UID_BASE')) or PODIUM_BLOCKS.get(key)
        if block is None:
            raise SystemExit(f'{key}: no uid block for the podium (PODIUM_BLOCKS)')
        for stage in ('STAGE1', 'STAGE2'):
            steps = cfg.get(stage, [])
            at = [i for i, st in enumerate(steps) if st[0] == 'port/mode_spawns.py']
            if at and (not any((st[0] == 'port/podium.py' for st in steps))):
                steps.insert(at[-1] + 1, podium_step(block, PODIUM_AT.get(key)))
with_podium(MAPS)
