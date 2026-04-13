# Plan session Windows — Capturer la cle de chiffrement

## Objectif
Intercepter la cle de chiffrement SM4 que le serveur GAMHS envoie au client LoL
avant le lancement d'un replay. Une fois la cle capturee, on peut decoder les
57 000 paquets de mouvement et avoir les positions de tous les joueurs chaque seconde.

## Prerequis Windows
1. Installer Wireshark : https://www.wireshark.org/download.html
2. Le client LoL doit etre installe et connecte
3. Cloner le repo : `git clone https://github.com/SebSusini/lol-ia-coaching.git`

## Etape 1 : Capture Wireshark

```
1. Ouvrir Wireshark
2. Selectionner l'interface reseau active (Wi-Fi ou Ethernet)
3. Commencer la capture
4. Dans le client LoL, cliquer pour lancer un replay
5. Attendre que le replay se lance
6. Arreter la capture Wireshark
```

### Filtrer dans Wireshark
Le traffic est HTTPS donc chiffre. Pour voir le contenu :

**Option A — SSLKEYLOGFILE (recommande)** :
```
1. Fermer le client LoL completement
2. Definir la variable d'environnement :
   set SSLKEYLOGFILE=C:\Users\TON_USER\sslkeys.log
3. Relancer le client LoL
4. Dans Wireshark : Edit > Preferences > Protocols > TLS
   → Pre-Master-Secret log filename : C:\Users\TON_USER\sslkeys.log
5. Maintenant Wireshark peut dechiffrer le HTTPS
6. Lancer un replay
7. Filtrer : http.request.uri contains "replay" or http.request.uri contains "metadata"
```

**Option B — Fiddler/mitmproxy (plus simple)** :
```
1. Installer Fiddler Classic : https://www.telerik.com/fiddler
2. Activer "Decrypt HTTPS traffic" dans les settings
3. Lancer le client LoL
4. Lancer un replay
5. Chercher dans Fiddler les requetes contenant "replay" ou "metadata" ou "GAMHS"
6. La reponse JSON devrait contenir un champ "encryptionKey" ou similaire
```

**Option C — Charles Proxy** :
Meme principe que Fiddler, alternative populaire.

### Ce qu'on cherche dans la reponse
Un champ JSON qui ressemble a :
```json
{
  "encryptionKey": "ABCDEFGHIJKLMNOPQRSTUVWXYZab==",
  "observerEncryptionKey": "...",
  "gameKey": "..."
}
```

Ou dans les headers de la reponse, un header custom avec la cle.

La cle devrait etre :
- 16 bytes en Base64 (24 caracteres Base64)
- Ou 32 bytes en hex (32 caracteres hex)

## Etape 2 : Tester la cle

Une fois la cle trouvee, sur Mac ou Windows :

```bash
cd lol-ia-coaching

# Tester le dechiffrement
python3 -c "
import base64
key_b64 = 'LA_CLE_BASE64_ICI'
key = base64.b64decode(key_b64)
print(f'Key: {key.hex()} ({len(key)} bytes)')
"
```

Puis utiliser le decoder :
```bash
python3 decoder/packet_decryptor.py replays/TON_REPLAY.rofl --sm4-key "LA_CLE_EN_HEX"
```

## Etape 3 : Automatiser

Une fois qu'on sait ou la cle apparait (quel endpoint, quel champ), on peut :
1. Ecrire un script qui fetch la cle automatiquement via l'API LCU locale
2. Ou intercepter la cle a chaque lancement de replay
3. Integrer dans le pipeline bin/review

## Alternative : API LCU locale

Le LeagueClient expose une API REST locale. On a teste sur Mac mais la cle
n'apparait pas dans les endpoints qu'on a essayes. Sur Windows, tester :

```bash
# Lire le lockfile pour obtenir port + token
type "C:\Riot Games\League of Legends\lockfile"
# Format: LeagueClient:PID:PORT:TOKEN:https

# Essayer ces endpoints pendant qu'un replay tourne :
curl -sk -u "riot:TOKEN" "https://127.0.0.1:PORT/lol-replays/v1/metadata/GAME_ID"
curl -sk -u "riot:TOKEN" "https://127.0.0.1:PORT/lol-gameflow/v1/session"
curl -sk -u "riot:TOKEN" "https://127.0.0.1:PORT/lol-login/v1/login-in-game-creds"
```

## Ce qu'on sait sur la crypto

### Architecture de chiffrement
```
Serveur GAMHS → cle SM4 (16 bytes) → LeagueClient → Game binary
                                                         ↓
ROFL2 file → chunks (zstd compressed) → blocks → packets
                                                     ↓
                                          SM4-CTR decrypt avec la cle
                                                     ↓
                                          Positions, degats, abilities
```

### Paquets de mouvement
- packet_id = 0x001c (28 decimal)
- 57 297 paquets dans un replay de 27 min
- Chiffres avec SM4-CTR
- Apres dechiffrement, format PathPacket :
  - u16 parsing_type
  - u32 entity_id (0x400000ae-0x400000b7 = 10 joueurs)
  - f32 speed (100-600)
  - Waypoints delta-compressed u16 pairs
  - x = sign_extend(raw, 16) * 2.0 + 7358.0
  - y = sign_extend(raw, 16) * 2.0 + 7412.0

### Paquets haute entropie (aussi chiffres, autres donnees)
- 0x00dc : 13 174 paquets (probablement damage/combat)
- 0x024f : 4 080 paquets
- 0x034a : 4 049 paquets
- 0x0061 : 543 paquets
- Meme cle SM4, meme mecanisme

## Infos de connexion

### Riot API
- Cle : dans .env du repo (expire toutes les 24h, renouveler sur developer.riotgames.com)
- Summoner : Banditacciu#EUW
- Region : europe / euw1
- PUUID : Jc2Min8hd7uy0rxN... (partial)

### Match IDs testes
- EUW1_7816865419 : Sylas vs Mel, 5/7/5, LOSS, 27min (replay .rofl disponible)
- EUW1_7816890699 : Sylas mid, 9/0/9, WIN, 25min

## Fichiers importants dans le repo

| Fichier | Role |
|---|---|
| decoder/sm4_decoder.py | Parser ROFL2 + SM4 implementation |
| decoder/packet_decryptor.py | HMAC-SM3 + analyse entropie |
| decoder/movement_decoder.py | Extraction paquets mouvement |
| decoder/batch_movement_parser.py | Analyse format batch |
| extractor/lib/ | Pipeline Ruby (12 detectors) |
| extractor/bin/record_replay | Enregistrement live pendant replay x8 |
| extractor/bin/fetch_timeline | Fetch Riot API timeline |
| prompts/review.md | Prompt Claude pour reviews |
| .env | Cle API Riot + pseudo |
| docs/NEXT_SESSION_BRIEFING.md | Briefing technique complet |

## Resume

On a TOUT le code pour decoder les paquets. Il manque UNE chose : les 16 bytes
de la cle SM4. Cette cle est envoyee par le serveur GAMHS au client LoL juste
avant le lancement du replay. Wireshark/Fiddler sur Windows permettra de la capturer.
