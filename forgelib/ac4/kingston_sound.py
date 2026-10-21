"""forgelib/ac4/kingston_sound.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def fnv1_32(name):
    h = 2166136261
    for c in name.lower().encode():
        h = h * 16777619 & 4294967295
        h ^= c
    return h
BANKS = {3125493002: 1033084844, 1133950525: 4026575155}
EVENTS = {'RFX_Kingston_Trees_Wind_NonSpa': 'RFX_Havana_Trees_Wind_NonSpa', 'RFX_Kingston_Trees_Wind': 'RFX_Havana_Trees_Wind', 'RFX_Kingston_Trees_Wind_35M': 'RFX_Havana_Trees_Wind', 'WE_Kingston_Storm_Trees_NonSpa': 'RFX_Havana_Trees_Wind_NonSpa', 'WE_Kingston_Storm_Trees': 'RFX_Havana_Trees_Wind', 'WE_Kingston_Storm_Trees_35M': 'RFX_Havana_Trees_Wind', 'WE_Kingston_Rain_On_Foliage_10M_DLC': 'WE_Havana_RainOnRocks_10M', 'WE_Kingston_Rain_On_Foliage_DLC': 'WE_Havana_RainOnRooftops', 'Pad_Kingston_Jungle_Weather': 'WE_Havana_RainOnRooftops', 'Pad_Kingston_Jungle_Day': 'RFX_Havana_Trees_Wind_NonSpa', 'Pad_Kingston_Jungle_Night': 'RFX_Havana_TOD_N_Frogs_Insects_LP', 'RFX_Kingston_Night_Frogs_Insects_LP': 'RFX_Havana_TOD_N_Frogs_Insects_LP', 'RFX_Kingston_TOD_N_Frogs': 'RFX_Havana_TOD_N_Frogs_Insects_LP', 'RFX_Kingston_TOD_N_Insects': 'RFX_Havana_TOD_N_Crickets_LP', 'RFX_Kingston_Insect_Ratchet': 'RFX_Havana_Flies', 'RFX_Kingston_Bees': 'RFX_Havana_Flies', 'RFX_Kingston_Flies': 'RFX_Havana_Flies', 'RFX_Kingston_Mosquitoes': 'RFX_Havana_Flies', 'RFX_Kingston_Rats': 'RFX_Havana_Rats', 'RFX_Kingston_Mango_Birds': 'RFX_Havana_Yellowbird', 'RFX_Kingston_Hummingbird': 'RFX_Havana_Yellowbird', 'RFX_Kingston_Parrots': 'RFX_Havana_Sun_Conure', 'RFX_Kingston_Exotic_Birds': 'RFX_Havana_Sun_Conure', 'RFX_Kingston_Jamaican_Birds': 'RFX_Havana_Doves', 'RFX_Kingston_Thrasher': 'RFX_Havana_Thrush_Hermit', 'RFX_Kingston_Thrush_Hermit': 'RFX_Havana_Thrush_Hermit', 'RFX_Kingston_Pewee': 'RFX_Havana_Peewee', 'RFX_Kingston_Barn_Swallow': 'RFX_Havana_Barn_Swallow', 'RFX_Kingston_Rooster': 'RFX_Havana_Chicken', 'RFX_Kingston_Rooster_NonSpa': 'RFX_Havana_Chicken', 'RFX_Kingston_TOD_D_Dogs': 'RFX_Havana_TOD_D_Dogs', 'RFX_Kingston_Church_Bells': 'RFX_Havana_TOD_D_Cathedral_Bells', 'RFX_Kingston_Blacksmith': 'RFX_Havana_Blacksmith', 'RFX_Kingston_Ship_Repairs_Ext': 'RFX_Havana_Ship_Repairs_Int', 'RFX_Kingston_Carpentry': 'RFX_Havana_Ship_Repairs_Int', 'RFX_Kingston_Harp': 'RFX_Havana_Harp', 'RFX_Kingston_Tankards': 'RFX_Havana_Tankards', 'RFX_Kingston_Broken_Glass': 'RFX_Havana_Broken_Glass', 'RFX_Kingston_Drunkard': 'RFX_Havana_Burps', 'RFX_Kingston_Pirates_Low': 'RFX_Havana_Pirate_Cheers', 'VOI_Kingston_Gamblers': 'RFX_Havana_Pirate_Cheers', 'RFX_Kingston_Stone_Footsteps': 'RFX_Havana_Footsteps_Stone', 'RFX_Kingston_TOD_N_Snoring1': 'RFX_Havana_Snoring_Male', 'RFX_Kingston_TOD_N_Snoring2': 'RFX_Havana_Snoring_Male', 'VOI_Kingston_Tavern_Lady': 'VOI_Havana_Women_Gossip', 'VOI_Kingston_Eng_Gen_Merchant1': 'VOI_Havana_Market_Eng_Gen_Merchant1', 'VOI_Kingston_Eng_Fish_Merch1': 'VOI_Havana_Market_Eng_Gen_Merchant1', 'VOI_Kingston_Irish_Fish_Merch_1': 'VOI_Havana_Market_Eng_Gen_Merchant1', 'VOI_Kingston_English_Tools_Merch1': 'VOI_Havana_Market_Spa_Tools_Merchant1', 'VOI_Kingston_Spanish_Fish_Merchant_02': 'VOI_Havana_Market_Spa_Gen_Merchant2', 'VOI_Kingston_Spa_Gen_Merchant3': 'VOI_Havana_Market_Spa_Gen_Merchant1'}

def _id(short):
    return fnv1_32('Play_AMB_' + short)
REMAP = dict(BANKS)
REMAP.update({_id(k): _id(v) for k, v in EVENTS.items()})

def _events(path):
    out = {}
    for line in open(path, encoding='utf-8'):
        m = re.match('(0x[0-9a-f]+)\\s+\\d+\\s+= (\\S+)', line)
        if m:
            out[int(m.group(1), 16)] = m.group(2)
    return out

def check():
    king = _events(os.path.join(ROOT, 'out/ac4/ki6/king_events.txt'))
    hava = _events(os.path.join(ROOT, 'out/ac4/ki6/hava_events.txt'))
    bad = [k for k in EVENTS if _id(k) not in king] + [v for v in set(EVENTS.values()) if _id(v) not in hava]
    left = sorted((n for i, n in king.items() if n.startswith('Play_AMB_') and i not in REMAP))
    print(f"{len(EVENTS)} events mapped onto {len(set(EVENTS.values()))} of Havana's {len(hava)}; names not found: {bad}")
    print(f'Kingston ambience left silent ({len(left)}): {left}')
    return not bad
if __name__ == '__main__':
    sys.exit(0 if check() else 1)
