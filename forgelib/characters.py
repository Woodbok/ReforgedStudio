"""forgelib/characters.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import dataclasses
import re
from dataclasses import dataclass, field

@dataclass
class Head:
    part: str
    slot: str
    table: str
    column: int
    row: int
    donor_row: int
    faceless: list
    bake: object = None

@dataclass
class NewSlot:
    table: str
    template: str
    name: str
    like: int = 1
    rows: tuple = (0,)
    like_table: str = None
    like_file: str = None

def slots_of(slot):
    return list(slot) if isinstance(slot, tuple) else [slot]

@dataclass
class Cloth:
    acr_cloth: str
    acr_mesh: str
    acr_settings: str
    acr_ragdoll: str
    material_part: str
    cloth_file: str
    mesh_file: str
    cloth_slot: object = None
    mesh_slot: object = None

@dataclass
class WeaponHang:
    acr_file: str
    acr_skeleton: str
    bone: int
    template: str
    correction: tuple = None
    table: str = 'WeaponAddonBasic'
    name: str = 'WeaponRef'
    rows: tuple = None

@dataclass
class Weapon:
    acr_file: str
    donor_id: int
    item: str
    name_ids: tuple = None
    icon: str = None
    weapon_class: int = None
    values: bool = True
    values_from: int = None

@dataclass
class StockWeapon:
    id: int
    file: str
    donor_id: int
    item: str
    icon: str = None
    icon_kw: dict = None
    name_ids: tuple = None
    weapon_class: int = None
    new_slot: str = None

@dataclass
class AddOnSkeleton:
    acr_file: str
    acr_skeleton: str
    table: str
    row: int
    column: int
    cell_from_row: int
    template: str
    name: str
    replace: bool = False
    like: int = None
    rows: tuple = None
    like_file: str = None

@dataclass
class MenuPort:
    acr_file: str
    key: int
    ac3_file: str

@dataclass
class MenuAction:
    ac3_file: str
    key: int
    item: int
    clip: tuple

@dataclass
class OwnEntity:
    slots: dict = field(default_factory=dict)
    files: tuple = ('Main_Player',)
    renew_nested: bool = False
    acr_animset: tuple = None
    face_rig: tuple = None
    hide_eyes: bool = False

@dataclass
class Voice:
    acr_file: str = None
    acr_set: str = None

@dataclass
class KillSet:
    acr_file: str
    acr_prefix: str
    name: str = 'Kill'
    cameras: bool = False
    own_gates: bool = True
    prune: bool = False
    faces: bool = False
    victim_faces: bool = False
    debug_swap: tuple = ()

@dataclass
class PartIcons:
    art: tuple
    boxes: dict
    badges_from: object = 'donor'
    render: dict = None
DEFAULT_CUT = 'Char{donor}_(Bodypart_\\w+_0[23]|Costume_(?!Empty$)\\w+|Color_0[23]|Face_\\w+|[Ww]arpaint[Cc]olor_\\d+|Challenge\\d|Move_Taunt_0[23]|Weapon_(?!01_01$)\\w+|Silent_(?!01$)\\w+)'
DEFAULT_HIDE = None

def cut_rx(c):
    if c.cut != 'default':
        return c.cut
    rx = DEFAULT_CUT
    if c.looks > 1:
        rx = DEFAULT_CUT.replace('Bodypart_\\w+_0[23]|', '')
        assert rx != DEFAULT_CUT
    return rx.format(donor=c.donor)

def hide_rx(c):
    if c.hide == 'default':
        return DEFAULT_HIDE.format(donor=c.donor) if DEFAULT_HIDE else None
    return c.hide

@dataclass
class Character:
    key: str
    title: str
    acr_file: str
    acr_prefix: str
    donor: int
    new: int
    copy: list
    parts: dict
    head: Head = None
    strip: dict = field(default_factory=dict)
    colours: tuple = None
    untinted: list = field(default_factory=list)
    untinted_template: str = None
    materials: list = field(default_factory=list)
    body_materials: dict = field(default_factory=dict)
    cloth: list = field(default_factory=list)
    weapon_hang: object = 'auto'
    class_hangs: object = None
    silent_hangs: object = None
    weapon: Weapon = None
    stock_weapon: object = 'auto'
    more_weapons: list = field(default_factory=list)
    addon_skeletons: object = 'auto'
    cut: str = 'default'
    hide: str = 'default'
    hide_rule: str = 'LOYALTY_DevTeam'
    face: bool = True
    winter_empty: bool = True
    portrait: tuple = None
    name_ids: tuple = None
    menu: dict = field(default_factory=dict)
    menu_ports: list = field(default_factory=list)
    own_entity: OwnEntity = None
    kill_set: KillSet = None
    voice: object = 'auto'
    bank: str = None
    part_icons: PartIcons = None
    item_texts: dict = field(default_factory=dict)
    pack: bool = True
    known_clashes: tuple = ()
    material_pairs: dict = field(default_factory=dict)
    acr_texts: dict = field(default_factory=dict)
    own_texts: dict = field(default_factory=dict)
    acr_part_files: tuple = ()
    static_face: tuple = ()
    face_skin: str = None
    looks: int = 1
    acr_extra_files: tuple = ()
    strip_columns: dict = field(default_factory=dict)
    donor_rule: bool = True
    acr_colours: tuple = None
    costume_adds: bool = False
    weapon_icons: dict = None
ACR_TAUNT = ('Game Fix', 'ID99_Free_Taunt_Custom02_Male', 2225284020)
MENU_TAUNT = ('Game Fix', 'ID99_Free_Taunt_Custom02_Male', 2937929606)
FREE1, FREE2 = (2567147248, 2567147263)

def _jester_mask():
    import mask_gold
    return mask_gold.bake(mask_gold.SHIPPED_LOOK)
JESTER = Character(key='jester', title='the Ottoman Jester (ACR persona 70) as persona 79, cloned from the Coyote Man (87)', acr_file='ACFE_ID70_Main_Player', acr_prefix='ACFE_ID70_', donor=87, new=79, copy=['MainPartTable', 'SecondaryPartTable', 'HeadTable', 'BeltTable', 'LegsTable', 'Winter', 'MainPart_V1', 'SecondaryPart_V1', 'Legs_v1', 'belt_V1', 'Hairs_V1', 'Masque_V2', 'Custom_color1', 'Costumes_Temp', 'WeaponAddonBasic', 'WeaponAddOnSilent', 'Softbody_SkirtFront', 'Skirt_Front'], parts={'Torso': 'MainPart_V1', 'Arms': 'SecondaryPart_V1', 'Legs': 'Legs_v1', 'Belt': 'belt_V1', 'Turban': 'Hairs_V1'}, head=Head(part='Head', slot='Masque_V2', table='HeadTable', column=8, row=0, donor_row=1, faceless=['Head_V1', 'Eyes'], bake=_jester_mask), strip={'MainPartTable': ['Back_Fur_V1'], 'Winter': ['Body_v1', 'Winter_v1'], 'HeadTable': ['Hairs_Alpha']}, colours=('ACFE_ID70_CustomColors', 'Custom_color1'), untinted=['MainPart_V1', 'SecondaryPart_V1', 'Legs_v1'], untinted_template='Legs_v1', materials=['Torso', 'Arms', 'Legs', 'Belt', 'Turban'], body_materials={'Turban': 'Legs_v1'}, cloth=[Cloth(acr_cloth='ACFE_ID70_FlapsCustom', acr_mesh='ACFE_ID70_Flaps', acr_settings='ID70_ClothSettings', acr_ragdoll='id14_Courtisane_Skirt_Ragdoll', material_part='Legs', cloth_file='Softbody_SkirtFront', mesh_file='Skirt_Front')], weapon=Weapon(acr_file='ACFE_Weapon_700101', donor_id=870101, item='Weapon_01_01', name_ids=(1524695042, 1524695043), icon='Jester'), addon_skeletons=[AddOnSkeleton(acr_file='ACFE_ID70_Main_Player', acr_skeleton='AC2MP_Middle_Shirt_Bend', table='SecondaryPartTable', row=0, column=11, cell_from_row=1, template='Main_Addon', name='Shirt_Bend')], cut='Char87_(Bodypart_\\w+_0[23]|Costume_(?!Empty$)\\w+|Color_0[23]|Face_\\w+|Warpaintcolor_\\d+)', hide='Char87_(Weapon_(?!01_01$)\\w+|Silent_(?!01$)\\w+|Move_Taunt_0[23])', portrait=('acfe_head_id70_MapDesc', 'acfe_head_id70_Map', (22, 24, 76)), name_ids=(1524695040, 1524695041), menu={'custom_idle': 453142435, 'select_idle': 3574582559, 'selected': 453143624, 'team_right': 'generic', 'team_left': 'generic', 'custom_taunt': 1031361020, 'select_taunt': 3770612405}, menu_ports=[MenuPort('ACFE_Menu_Common', 453142435, 'AC3MP_Menu_Common'), MenuPort('ACFE_Menu_GotoGame', 453143624, 'AC3MP_Menu_GotoGame'), MenuAction('AC3MP_Menu_Common', 1031361020, 1180843589, MENU_TAUNT), MenuAction('AC3MP_Menu_GotoGame', 3770612405, 2030086100, MENU_TAUNT)], own_entity=OwnEntity(slots={FREE1: ACR_TAUNT}, files=('Main_Player', 'Main_NPC'), acr_animset=('ACFE_ID70_Main_Player', 'ACFE_ID70_AnimSet'), renew_nested=True), kill_set=KillSet(acr_file='ACFE_ID70', acr_prefix='ID70_', cameras=True), part_icons=PartIcons(art=('Jester_MapDesc',), boxes={'Head': (106, 48, 158, 100), 'MainPart': (98, 106, 182, 190), 'SecondPart': (158, 168, 214, 224), 'Legs': (48, 344, 112, 408), 'Belt': (98, 188, 162, 252)}, badges_from=None), item_texts={'Move_Taunt_01': (None, 1524695044), 'Bodypart_Head_01': (1524695069, 1524695070), 'Bodypart_MainPart_01': (1524695071, 1524695072), 'Bodypart_SecondPart_01': (1524695073, 1524695074), 'Bodypart_Belt_01': (1524695075, 1524695076), 'Bodypart_Legs_01': (1524695077, 1524695078), 'Color_01': (None, 1524695079), 'Move_Finish_01': (None, 1524695080), 'Move_Finish_02': (None, 1524695080), 'Move_Finish_03': (None, 1524695080), 'Move_Stun_01': (None, 1524695081), 'Move_Stun_02': (None, 1524695081), 'Move_Stun_03': (None, 1524695081), 'Silent_01': (None, 1524695082)}, known_clashes=(2092978496,))
DOCTOR_TAUNT = ('Game Fix', 'ID99_Free_Taunt_Custom01_Male', 377473363)
DOCTOR_MENU_TAUNT = ('Game Fix', 'ID99_Free_Taunt_Custom01_Male', 3268966133)

def _doctor_skeleton(acr_skeleton, table, column, name, replace=False):
    return AddOnSkeleton(acr_file='ACFE_ID71_Main_player', acr_skeleton=acr_skeleton, table=table, row=0, column=column, cell_from_row=1, template='Main_Addon', name=name, replace=replace)
DOCTOR = Character(key='doctor', title='the Doctor (ACR persona 71) as persona 98, cloned from the Coyote Man (87)', acr_file='ACFE_ID71_Main_player', acr_prefix='ACFE_ID71_', donor=87, new=98, copy=['MainPartTable', 'SecondaryPartTable', 'HeadTable', 'BeltTable', 'LegsTable', 'Winter', 'MainPart_V1', 'SecondaryPart_V1', 'Legs_v1', 'belt_V1', 'Back_Fur_V1', 'Custom_color1', 'Costumes_Temp', 'WeaponAddonBasic', 'WeaponAddOnSilent', 'Softbody_SkirtFront', 'Skirt_Front'], parts={'buste_custom': 'MainPart_V1', 'custom_Shoulder': 'SecondaryPart_V1', 'hat_customs': 'Legs_v1', 'Sides_customs': 'belt_V1', 'mask_custom': 'Back_Fur_V1'}, head=None, strip={'Winter': ['Body_v1', 'Winter_v1'], 'HeadTable': ['Hairs_Alpha', 'Hairs_V1', 'Head_V1', 'Eyes']}, body_materials={'mask_custom': 'Legs_v1'}, colours=None, untinted=['MainPart_V1', 'SecondaryPart_V1', 'Legs_v1', 'belt_V1'], untinted_template='Legs_v1', materials=['buste_custom', 'custom_Shoulder', 'hat_customs', 'Sides_customs', 'mask_custom'], cloth=[Cloth(acr_cloth='ACFE_ID71-Dress-custom', acr_mesh='ACFE_ID71_newrobe_custom', acr_settings='ACFE_ID71_Dress_Custom_settings', acr_ragdoll='ID71_Skirt_Ragdoll', material_part='hat_customs', cloth_file='Softbody_SkirtFront', mesh_file='Skirt_Front')], weapon=Weapon(acr_file='ACFE_Weapon_710101', donor_id=870101, item='Weapon_01_01', name_ids=(1524695047, 1524695048), icon='Doctor'), addon_skeletons=[_doctor_skeleton('AC2MP_Middle_Shirt_Bend', 'SecondaryPartTable', 11, 'Shirt_Bend'), _doctor_skeleton('AC2_Skirt', 'MainPartTable', 5, 'Skirt', replace=True), _doctor_skeleton('ac2mp_id05_addon_sacoche_01', 'SecondaryPartTable', 8, 'Sacoche_01', replace=True), _doctor_skeleton('ac2mp_id05_addon_sacoche02', 'HeadTable', 6, 'Sacoche_02', replace=True)], cut='Char87_(Bodypart_\\w+_0[23]|Costume_(?!Empty$)\\w+|Color_0[23]|Face_\\w+|Warpaintcolor_\\d+)', hide='Char87_(Weapon_(?!01_01$)\\w+|Silent_(?!01$)\\w+|Move_Taunt_0[23])', portrait=('acfe_head_id71_MapDesc', 'acfe_head_id71_Map', (26, 12, 76)), name_ids=(1524695045, 1524695046), acr_texts={1524695045: 294826, 1524695046: 315987}, menu={'custom_idle': 453142436, 'select_idle': 3574582559, 'selected': 453143623, 'team_right': 'generic', 'team_left': 'generic', 'custom_taunt': 3060178788, 'select_taunt': 3485480092}, menu_ports=[MenuPort('ACFE_Menu_Common', 453142436, 'AC3MP_Menu_Common'), MenuPort('ACFE_Menu_GotoGame', 453143623, 'AC3MP_Menu_GotoGame'), MenuAction('AC3MP_Menu_Common', 3060178788, 3341040978, DOCTOR_MENU_TAUNT), MenuAction('AC3MP_Menu_GotoGame', 3485480092, 301734250, DOCTOR_MENU_TAUNT)], own_entity=OwnEntity(slots={FREE1: DOCTOR_TAUNT}, files=('Main_Player', 'Main_NPC'), acr_animset=('ACFE_ID71_Main_player', 'ACFE_ID71_AnimSet'), renew_nested=True), kill_set=KillSet(acr_file='ACFE_ID71', acr_prefix='ID71_', cameras=True, prune=True), bank='BNK_CHR_MP_ID71', part_icons=PartIcons(art=('Doctor_MapDesc',), boxes={'Head': (106, 38, 166, 98), 'MainPart': (86, 96, 170, 180), 'SecondPart': (150, 96, 206, 152), 'Legs': (84, 330, 164, 410), 'Belt': (96, 186, 160, 250)}, badges_from=None), item_texts={'Move_Taunt_01': (None, 1524695049), 'Bodypart_Head_01': (1524695083, 1524695084), 'Bodypart_MainPart_01': (1524695085, 1524695086), 'Bodypart_SecondPart_01': (1524695087, 1524695088), 'Bodypart_Belt_01': (1524695089, 1524695090), 'Bodypart_Legs_01': (1524695091, 1524695092), 'Color_01': (None, 1524695093), 'Move_Finish_01': (None, 1524695094), 'Move_Finish_02': (None, 1524695094), 'Move_Finish_03': (None, 1524695094), 'Move_Stun_01': (None, 1524695095), 'Move_Stun_02': (None, 1524695095), 'Move_Stun_03': (None, 1524695095), 'Silent_01': (None, 1524695096)})
COURTESAN_TAUNT = ('ACFE_ID72_Main_Player', 'ID14_Taunt_01', 372033030)
COURTESAN_MENU_TAUNT = ('ACFE_ID72_Main_Player', 'ID14_Taunt_01', 1609949141)
COURTESAN = Character(key='courtesan', title='the Courtesan (ACR persona 72) as persona 89, cloned from Lady Maverick (75)', acr_file='ACFE_ID72_Main_Player', acr_prefix='ACFE_ID72_', donor=75, new=89, copy=['MainPartTable', 'SecondaryPartTable', 'HeadTable', 'BeltTable', 'LegsTable', 'Winter', 'MainPart_V1', 'SecondaryPart_V1', 'SecondaryPart_Shorty_V1', 'SecondaryPart_Softbody', 'Belt_V1', 'Legs_V1', 'Hair_V1', 'Custom_color1', 'Costumes_Temp', 'WeaponAddonBasic', 'WeaponAddOnSilent'], parts={'body_Custom': 'SecondaryPart_V1', 'hair': 'Hair_V1', 'sacoche_Custom': 'MainPart_V1', 'bandes_Custom': 'Belt_V1', 'Colier_Custom': 'Legs_V1', 'head': NewSlot(table='MainPartTable', template='AC3MP_ID76_Body', name='Head'), 'Eyes': NewSlot(table='MainPartTable', template='AC3MP_ID87_Eyes', name='Eyes'), 'meches': 'Cils_V1', 'bracelet_Custom': NewSlot(table='SecondaryPartTable', template='AC3MP_ID75_Belt_V1', name='Bracelet'), 'fil_custom': NewSlot(table='BeltTable', template='AC3MP_ID75_Belt_V1', name='Fil'), 'rose_custom': NewSlot(table='LegsTable', template='AC3MP_ID75_Legs_V1', name='Rose')}, material_pairs={'ACFE_ID72_01_Custom': 'AC3MP_ID75_V1_SecondaryPart', 'ACFE_ID72_02_Custom': 'AC3MP_ID75_V1_Backface'}, strip={'HeadTable': ['Head_V1', 'V1_Head_Set', 'V1_BouclesOreilles']}, colours=None, untinted=['MainPart_V1', 'SecondaryPart_V1', 'Belt_V1', 'Legs_V1', 'Bracelet', 'Fil', 'Rose'], untinted_template='Legs_V1', materials=['body_Custom', 'hair', 'meches', 'sacoche_Custom', 'bandes_Custom', 'Colier_Custom', 'head', 'Eyes', 'bracelet_Custom', 'fil_custom', 'rose_custom'], cloth=[Cloth(acr_cloth='ACFE_ID72_robe_Custom', acr_mesh='ACFE_ID72_robe_Custom', acr_settings='ACFE_ID72_ClothSettings', acr_ragdoll='ACFE_ID72_Skirt_Ragdoll', material_part=None, cloth_file='SecondaryPart_Softbody', mesh_file='SecondaryPart_Shorty_V1')], weapon=None, stock_weapon=StockWeapon(id=720101, file='ACFE_Weapon_720101_RIGGED', donor_id=750101, item='Weapon_01_01', icon='Courtesan', icon_kw={'axes': ('x', '-z', 'y'), 'angle': 0.0, 'fill': 0.5, 'focus': 0.58, 'lift': 2.2}, name_ids=(1524695052, 1524695053)), portrait=('Courtesane_MapDesc', 'Courtesane_Map', (0, 0, 64), 'AC3'), name_ids=(1524695050, 1524695051), acr_texts={1524695050: 294825, 1524695051: 315991}, menu={'custom_idle': 453142437, 'selected': 453143622, 'team_right': 'generic', 'team_left': 'generic', 'custom_taunt': 4285157440, 'select_taunt': 2589782515}, menu_ports=[MenuPort('ACFE_Menu_Common', 453142437, 'AC3MP_Menu_Common'), MenuPort('ACFE_Menu_GotoGame', 453143622, 'AC3MP_Menu_GotoGame'), MenuAction('AC3MP_Menu_Common', 4285157440, 2881718159, COURTESAN_MENU_TAUNT), MenuAction('AC3MP_Menu_GotoGame', 2589782515, 3781278001, COURTESAN_MENU_TAUNT)], own_entity=OwnEntity(slots={FREE1: COURTESAN_TAUNT}, files=('Main_Player', 'Main_NPC'), acr_animset=('ACFE_ID72_Main_Player', 'ACFE_ID72_AnimSet'), renew_nested=True), kill_set=KillSet(acr_file='ACFE_ID72', acr_prefix='ID72_', cameras=True, prune=True), bank='BNK_CHR_MP_ID72', part_icons=PartIcons(art=('Courtesan_MapDesc',), boxes={'Head': (104, 34, 164, 94), 'MainPart': (96, 100, 180, 184), 'SecondPart': (66, 262, 186, 382), 'Legs': (96, 376, 160, 440), 'Belt': (104, 196, 172, 264)}), item_texts={'Bodypart_Head_01': (1524695054, 1524695055), 'Bodypart_MainPart_01': (1524695056, 1524695057), 'Bodypart_SecondPart_01': (1524695058, 1524695059), 'Bodypart_Belt_01': (1524695060, 1524695061), 'Bodypart_Legs_01': (1524695062, 1524695063), 'Color_01': (None, 1524695064), 'Move_Finish_01': (None, 1524695065), 'Move_Finish_02': (None, 1524695065), 'Move_Finish_03': (None, 1524695065), 'Move_Stun_01': (None, 1524695066), 'Move_Stun_02': (None, 1524695066), 'Move_Stun_03': (None, 1524695066), 'Move_Taunt_01': (None, 1524695067), 'Silent_01': (None, 1524695068)})
CORSAIR = Character(key='corsair_hand', title='the Corsair (ACR persona 58) as persona 101, cloned from the Red Coat (83) - the hand config of CS1-CS14', acr_file='ACFE_ID58_Main_Player', acr_prefix='ACFE_ID58_', donor=83, new=101, acr_part_files=('ACFE_ID58_Head_V1', 'ACFE_ID58_Hair_V1', 'ACFE_ID58_Eyes', 'ACFE_ID58_Torso_V1', 'ACFE_ID58_Arms_V1', 'ACFE_ID58_Legs_V1', 'ACFE_ID58_Belt_V1', 'ACFE_ID58_SlotMisc_V1', 'human_LongHair', 'ACFE_ID58_HeadMisc_LOD0', 'ACFE_ID58_Head_V2', 'ACFE_ID58_Head_V3', 'ACFE_ID58_Hair_V2', 'ACFE_ID58_Hat_V2', 'ACFE_ID58_Hat_V3', 'ACFE_ID58_Torso_V2', 'ACFE_ID58_Torso_V3', 'ACFE_ID58_Arms_V2', 'ACFE_ID58_Arms_V3', 'ACFE_ID58_Legs_V2', 'ACFE_ID58_Legs_V3', 'ACFE_ID58_Belt_V2', 'ACFE_ID58_Belt_V3', 'ACFE_ID58_Belt_V3_Softbody', 'ACFE_ID58_Belt_V3_tissu', 'ACFE_ID58_Belt_V3_Tissu', 'ACFE_ID58_SlotMisc_V2', 'ACFE_ID58_SlotMisc_V3'), acr_extra_files=('ACFE_ID55_Main_NPC',), looks=3, copy=['MainPartTable', 'SecondaryPartTable', 'HeadTable', 'BeltTable', 'LegsTable', 'Winter', 'MainPart_V1', 'SecondaryPart_V1', 'Belt_V1', 'Legs_V1', 'Hair_V1', 'Custom_color1', 'Costumes_Temp', 'WeaponAddonBasic', 'WeaponAddOnSilent', 'MainPart_V2', 'MainPart_V3', 'SecondaryPart_V2', 'SecondaryPart_V3', 'Belt_V2', 'Belt_V3', 'Legs_V2', 'Legs_V3', 'Hair_V2', 'Hair_V3'], parts={'Torso_V1': 'MainPart_V1', 'Torso_V2': 'MainPart_V2', 'Torso_V3': 'MainPart_V3', 'Arms_V1': 'SecondaryPart_V1', 'Arms_V2': 'SecondaryPart_V2', 'Arms_V3': 'SecondaryPart_V3', 'Legs_V1': 'Legs_V1', 'Legs_V2': 'Legs_V2', 'Legs_V3': 'Legs_V3', 'Belt_V1': 'Belt_V1', 'Belt_V2': 'Belt_V2', 'Belt_V3': 'Belt_V3', 'Hair_V1': ('Hair_V1', 'Hair_V3'), 'Hair_V2': 'Hair_V2', 'Head_V1': NewSlot(table='HeadTable', template='AC3MP_ID76_Body', name='Head', like=5), 'Head_V2': NewSlot(table='HeadTable', template='AC3MP_ID76_Body', name='Head_V2', like=5, rows=(1,)), 'Head_V3': NewSlot(table='HeadTable', template='AC3MP_ID76_Body', name='Head_V3', like=5, rows=(2,)), 'Eyes': NewSlot(table='HeadTable', template='AC3MP_ID87_Eyes', name='Eyes', like=5, rows=(0, 1, 2)), 'ACFE_Generic_Teeth_Female': NewSlot(table='HeadTable', template='AC3MP_ID76_Body', name='Teeth', like=5, rows=(0, 1, 2)), 'HeadMisc': NewSlot(table='HeadTable', template='AC3MP_ID76_Body', name='HeadBand', like=5), 'Hat_V2': NewSlot(table='HeadTable', template='AC3MP_ID83_Belt_V1', name='Hat_V2', like=5, rows=(1,)), 'Hat_V3': NewSlot(table='HeadTable', template='AC3MP_ID83_Belt_V1', name='Hat_V3', like=5, rows=(2,)), 'SlotMisc_V1': NewSlot(table='Costumes_Temp', template='AC3MP_ID83_SecondaryPart_V1', name='Coat', like_table='Costumes_Temp', rows=(1,)), 'SlotMisc_V2': NewSlot(table='Costumes_Temp', template='AC3MP_ID83_SecondaryPart_V1', name='Coat_V2', like_table='Costumes_Temp', rows=(2,)), 'SlotMisc_V3': NewSlot(table='Costumes_Temp', template='AC3MP_ID83_SecondaryPart_V1', name='Coat_V3', like_table='Costumes_Temp', rows=(3,))}, cloth=[Cloth(acr_cloth='ACFE_ID58_Belt_V3_Softbody', acr_mesh='ACFE_ID58_Belt_V3_tissu', acr_settings='ACFE_ID58_Belt_ClothSettings', acr_ragdoll='ID58_Skirt_Ragdoll', material_part='Belt_V3', cloth_file='BeltCloth', mesh_file='BeltClothMesh', cloth_slot=NewSlot(table='BeltTable', template='AC3MP_ID83_Cape_ULC_Softbody', name='BeltCloth', like=17, like_table='Costumes_Temp', rows=(2,)), mesh_slot=NewSlot(table='BeltTable', template='AC3MP_ID83_CostumeULC_Cape', name='BeltClothMesh', rows=(2,)))], strip={'HeadTable': ['Head_V1', 'V1_Head_Set', 'V2_Head_Set', 'V3_Head_Set', 'Cils'], 'Costumes_Temp': ['Main_V1', 'MainPart_V1_Costume', 'Head_V1', 'Hair_V1', 'MainPart_Costume_1_Set', 'MainPart_Costume_2_Set', 'MainPart_Costume_3_Set', 'V1_Head_Set', 'Cils']}, colours=None, untinted=['MainPart_V1', 'MainPart_V2', 'MainPart_V3', 'SecondaryPart_V1', 'SecondaryPart_V2', 'SecondaryPart_V3', 'Belt_V1', 'Belt_V2', 'Belt_V3', 'Legs_V1', 'Legs_V2', 'Legs_V3', 'Coat', 'Coat_V2', 'Coat_V3', 'Hat_V2', 'Hat_V3'], untinted_template='Legs_V1', materials=['Torso_V1', 'Torso_V2', 'Torso_V3', 'Arms_V1', 'Arms_V2', 'Arms_V3', 'Legs_V1', 'Legs_V2', 'Legs_V3', 'Belt_V1', 'Belt_V2', 'Belt_V3', 'Hair_V1', 'Hair_V2', 'Head_V1', 'Head_V2', 'Head_V3', 'Eyes', 'ACFE_Generic_Teeth_Female', 'SlotMisc_V1', 'SlotMisc_V2', 'SlotMisc_V3', 'HeadMisc', 'Hat_V2', 'Hat_V3'], stock_weapon=StockWeapon(id=580301, file='ACFE_weapon_580301', donor_id=830301, item='Weapon_01_01'), more_weapons=[StockWeapon(id=580302, file='ACFE_weapon_580302', donor_id=830302, item='Weapon_01_02'), StockWeapon(id=990301, file='ACFE_Weapon_990301', donor_id=830303, item='Weapon_01_03'), StockWeapon(id=580201, file='ACFE_weapon_580201', donor_id=830901, item='Weapon_02_01', weapon_class=2), StockWeapon(id=990201, file='ACFE_Weapon_990201', donor_id=830902, item='Weapon_02_02', weapon_class=2), StockWeapon(id=580101, file='ACFE_weapon_580101', donor_id=830101, item='Weapon_03_01'), StockWeapon(id=990101, file='ACFE_weapon_990101', donor_id=830102, item='Weapon_03_02')], cut='Char83_(Costume_(?!Empty$|Monet_01$|WolfPack$|Monet_02$)\\w+|Face_\\w+|[Ww]arpaint[Cc]olor_\\d+|Challenge\\d)', menu={'custom_idle': 453142409, 'selected': 453143602, 'team_right': 'generic', 'team_left': 'generic'}, menu_ports=[MenuPort('ACFE_Menu_Common', 453142409, 'AC3MP_Menu_Common'), MenuPort('ACFE_Menu_GotoGame', 453143602, 'AC3MP_Menu_GotoGame')], own_entity=OwnEntity(files=('Main_Player', 'Main_NPC'), acr_animset=('ACFE_ID58_Main_Player', 'ACFE_ID58_AnimSet'), renew_nested=True, face_rig=('ACFE_ID58_Head', 'ACFE_ID58_Head')), kill_set=KillSet(acr_file='ACFE_ID58', acr_prefix='ID58_', cameras=True, prune=True, faces=True), bank='BNK_CHR_MP_ID58', portrait=('acfe_head_id58_MapDesc', 'acfe_head_id58_Map', (26, 12, 76)), part_icons=PartIcons(art=('favoriteSkin_ID58_MapDesc',), boxes={'Head': (232, 92, 292, 152), 'MainPart': (214, 126, 306, 218), 'SecondPart': (196, 208, 260, 272), 'Legs': (200, 340, 296, 436), 'Belt': (224, 226, 296, 298)}))
CHARACTERS = {c.key: c for c in (JESTER, DOCTOR, COURTESAN, CORSAIR)}

def with_legacy(base=None):
    import legacy_config
    CHARACTERS.update(legacy_config.characters_of_roster(base))
    return CHARACTERS
HANG_BONE = 1563041187
HANG_TEMPLATE = 'ACFE_WeaponRef_ID99_WB01'

def auto_hang(c):
    import struct
    import own_entity
    name = f'{c.acr_prefix}WeaponRef'
    objs = own_entity.acr_objects(c.acr_file)
    if any((o['name'] == name for o in objs)):
        return WeaponHang(acr_file=c.acr_file, acr_skeleton=name, bone=HANG_BONE, template=HANG_TEMPLATE)
    key = struct.pack('<II', own_entity.BONE, HANG_BONE)
    held = [o['name'] for o in objs if key in o['body'] and o['name'] != 'Human Body']
    if held:
        assert len(held) == 1, f'{c.acr_file}: the hang bone in {held}'
        return WeaponHang(acr_file=c.acr_file, acr_skeleton=held[0], bone=HANG_BONE, template=HANG_TEMPLATE)
    return None

def auto_class_hangs(c):
    import legacy_config
    n = int(re.search('ID(\\d+)_', c.acr_prefix).group(1))
    table = next((t for t in legacy_config.load_survey(n)['tables'] if t['record'].endswith('AddonBasic')), None)
    if table is None:
        return []
    rigs = {}
    for k, row in enumerate(table['rows']):
        for cell in row:
            if re.fullmatch(f'ACFE_WeaponRef_ID{n}_WB0\\d', cell.get('file') or '', re.I):
                rigs.setdefault((cell['file'], cell['object']), []).append(k)
    return [WeaponHang(acr_file=f, acr_skeleton=o, bone=HANG_BONE, template=HANG_TEMPLATE, name=re.sub('^ACFE_WeaponRef_ID\\d+_', 'WeaponRef_', f), rows=tuple(rows)) for (f, o), rows in rigs.items()]
SILENT_BONE = 3190727509
SILENT_TEMPLATES = ('ACFE_WeaponRef_ID99_WS21', 'ACFE_WeaponRef_ID99_WS22', 'ACFE_WeaponRef_ID99_WS23')

def rig_bones(body):
    import struct
    import own_entity
    key, out = (struct.pack('<I', own_entity.BONE), [])
    p = body.find(key)
    while p >= 0:
        bid = struct.unpack_from('<I', body, p + 4)[0]
        q = p + 8 + (5 if body[p + 8] == 2 else 0) + 1
        out.append((bid, struct.unpack_from('<16f', body, q)))
        p = body.find(key, p + 4)
    return out

def same_rig(a, b, tol=1e-05):
    if [x for x, _t in a] != [x for x, _t in b]:
        return False
    for (_x, ta), (_y, tb) in zip(a, b):
        for k in (0, 8):
            if max((abs(u - v) for u, v in zip(ta[k:k + 3], tb[k:k + 3]))) > tol:
                return False
            qa, qb = (ta[k + 4:k + 8], tb[k + 4:k + 8])
            if min(max((abs(u - v) for u, v in zip(qa, qb))), max((abs(u + v) for u, v in zip(qa, qb)))) > tol:
                return False
    return True

def acr_silent_rigs(n):
    import legacy_config
    import own_entity
    table = next((t for t in legacy_config.load_survey(n)['tables'] if re.search('silent', t['record'], re.I)), None)
    out = []
    for k, row in enumerate(table['rows'] if table else []):
        for cell in row:
            f = cell.get('file') or ''
            if k >= len(SILENT_TEMPLATES) or not re.fullmatch(f'ACFE_WeaponRef_ID{n}_WS2\\d\\w*', f, re.I):
                continue
            own = rig_bones(next((o['body'] for o in own_entity.acr_objects(f) if o['name'] == cell['object'])))
            shared = rig_bones(next((o['body'] for o in own_entity.acr_objects(SILENT_TEMPLATES[k]) if o['name'] == SILENT_TEMPLATES[k])))
            if same_rig(own, shared):
                continue
            att = dict(own).get(SILENT_BONE)
            if [x for x, _t in own] != [x for x, _t in shared] or att is None or (not same_rig([(0, att[:8] * 2)], [(0, att[8:] * 2)])):
                raise ValueError(f"ACR {f}: an own silent rig hang_weapon cannot carry (bones {[hex(x) for x, _t in own]} vs {SILENT_TEMPLATES[k]}'s; its WeaponRef_Silent must hang off the root) - port it as an AddOnSkeleton (the AC4 mounts' route)")
            out.append((k, f, cell['object']))
    return out

def auto_silent_hangs(c):
    n = int(re.search('ID(\\d+)_', c.acr_prefix).group(1))
    return [WeaponHang(acr_file=f, acr_skeleton=o, bone=SILENT_BONE, template=SILENT_TEMPLATES[k], table='WeaponAddOnSilent', name=f'WeaponRef_WS{21 + k}', rows=(k,)) for k, f, o in acr_silent_rigs(n)]

def auto_stock_weapon(c):
    import os
    import struct
    import uid_index
    from bootstrap_build import Bootstrap
    from paths import AC3_SOURCE
    acr_id = int(re.search('ID(\\d+)_', c.acr_prefix).group(1))
    wid = acr_id * 10000 + 101
    boot = Bootstrap(os.path.join(AC3_SOURCE, 'DataPC.forge'))
    item = next((o for o in boot.objects if o['name'] == str(wid)), None)
    if item is None:
        return None
    idx, _ = uid_index.load('AC3')
    root = struct.unpack_from('<I', item['body'], 35)[0]
    if root not in idx:
        return None
    kit = next((o for o in boot.objects if o['name'] == f'CharacterBuildingKit_AC3MP_ID{c.donor}'))
    return StockWeapon(id=wid, file=idx[root][0], donor_id=struct.unpack_from('<I', kit['body'], 172)[0], item='Weapon_01_01')

def _fresh_id(label, taken):
    h = 2166136261
    for ch in label.encode():
        h = (h ^ ch) * 16777619 & 4294967295
    while h in taken or h < 268435456 or 4026531840 <= h < 4160749568:
        h = h * 16777619 + 2654435769 & 4294967295
    taken.add(h)
    return h
FREE3 = 2567147278

def auto_taunt(c):
    import os
    import uid_index
    import own_entity
    import make_jester_79 as g2
    changes = {}
    if not (c.own_entity and c.own_entity.acr_animset) or FREE1 in c.own_entity.slots:
        return changes
    acr_file, aset_name = c.own_entity.acr_animset
    objs = own_entity.acr_objects(acr_file)
    names = {g2.clip_id(o['body']): o['name'] for o in objs if o['body'][:2] == b'\x01\x02'}
    aset = next((o for o in objs if o['name'] == aset_name))
    slots = own_entity.animset_slots(aset['body'])
    taunts = sorted({names[clip] for _k, (_at, clip) in slots.items() if 'aunt' in names.get(clip, '')})
    if not taunts:
        return changes
    free = [names.get(slots[k][1], '') if k in slots else '' for k in (FREE1, FREE2, FREE3)]
    per_index = all(('aunt' in n for n in free)) and len(set(free)) == 3
    clip_id = next((i for i, n in names.items() if n == taunts[0]))
    a3, _ = uid_index.load('AC3')
    ar, _ = uid_index.load('ACR')
    here = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'characters.py'), encoding='utf-8').read()
    taken = set(a3) | set(ar) | {int(x, 16) for x in re.findall('0x([0-9A-Fa-f]{8})', here)}
    if clip_id in a3:
        clip_id = _fresh_id(f'{c.key}:taunt', taken)
    changes['own_entity'] = dataclasses.replace(c.own_entity, slots={**c.own_entity.slots, FREE1: (acr_file, taunts[0], clip_id)})
    if 'custom_taunt' not in c.menu and 'select_taunt' not in c.menu:
        menu_clip = (acr_file, taunts[0], _fresh_id(f'{c.key}:menu_taunt_clip', taken))
        keys = {sel: (_fresh_id(f'{c.key}:{sel}', taken), _fresh_id(f'{c.key}:{sel}:item', taken)) for sel in ('custom_taunt', 'select_taunt')}
        if not per_index:
            changes['menu'] = {**c.menu, **{sel: k for sel, (k, _i) in keys.items()}}
            changes['menu_ports'] = list(c.menu_ports) + [MenuAction('AC3MP_Menu_Common', *keys['custom_taunt'], menu_clip), MenuAction('AC3MP_Menu_GotoGame', *keys['select_taunt'], menu_clip)]
            return changes
        clips = [(acr_file, free[0], menu_clip[2])] + [(acr_file, free[i], _fresh_id(f'{c.key}:menu_taunt_clip{i + 1}', taken)) for i in (1, 2)]
        per = {sel: [keys[sel]] + [(_fresh_id(f'{c.key}:{sel}:{i + 1}', taken), _fresh_id(f'{c.key}:{sel}:{i + 1}:item', taken)) for i in (1, 2)] for sel in ('custom_taunt', 'select_taunt')}
        changes['menu'] = {**c.menu, **{sel: tuple((k for k, _i in ki)) for sel, ki in per.items()}}
        changes['menu_ports'] = list(c.menu_ports) + [MenuAction(f, *per[sel][i], clips[i]) for sel, f in (('custom_taunt', 'AC3MP_Menu_Common'), ('select_taunt', 'AC3MP_Menu_GotoGame')) for i in range(3)]
    return changes

def resolve(c):
    changes = auto_taunt(c)
    if c.weapon_hang == 'auto':
        changes['weapon_hang'] = auto_hang(c)
    if c.class_hangs == 'auto':
        changes['class_hangs'] = auto_class_hangs(c)
    if c.silent_hangs == 'auto':
        changes['silent_hangs'] = auto_silent_hangs(c)
    if c.stock_weapon == 'auto':
        changes['stock_weapon'] = None if c.weapon else auto_stock_weapon(c)
    return dataclasses.replace(c, **changes) if changes else c
