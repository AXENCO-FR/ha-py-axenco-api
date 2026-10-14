# PyAxencoAPI

Client Python asynchrone pour interagir avec les dispositifs connectés MyNeomitis via API REST/Websocket.

## Installation

```bash
pip install pyaxencoapi
```

Python **3.14 ou supérieur** est requis. Python 3.11, 3.12 et 3.13 ne sont
plus pris en charge.

## Objets publics et migration

Cette évolution rompt le contrat public : les dictionnaires
de dispositifs sont remplacés par des `AxencoDevice` et les erreurs réseau,
d'authentification et de réponse deviennent des exceptions explicites.
Cette rupture est versionnée en `2.0.0`. La disponibilité sur PyPI dépend de la
publication de cette version par un mainteneur.

`get_devices(force=False)` retourne une liste de `AxencoDevice` et utilise un
cache de cinq minutes avec horloge monotone ; une liste vide signifie un inventaire
réellement vide. `get_device_state()` retourne `AxencoState`. `subscribe(callback)`
retourne une désinscription idempotente, autorise plusieurs callbacks et les isole
en cas d'erreur. Les patches ne sont appliqués qu'à l'appareil concerné.
La connexion physique de chaque enfant reste indépendante ;
`is_device_available(id)` combine sa connexion et celles de ses parents connus,
sans modifier les snapshots. Le consommateur combine ce résultat avec le transport
et réévalue les enfants lorsqu'un événement parent arrive.

`link`, `discover` et les événements incomplets réconcilient l'inventaire REST.
Chaque connexion/reconnexion réconcilie les états avant d'annoncer le transport
disponible. Les en-têtes Socket.IO sont recalculés avec les jetons courants.
Une réconciliation transitoirement échouée après reconnexion est retentée avec
backoff, uniquement jusqu'au succès (ou à l'arrêt/refus d'authentification).
Les requêtes REST sont bornées à dix secondes, le setup Socket.IO à quarante-cinq
et attend la réconciliation initiale. Seul un refus explicite de namespace ou un
HTTP 401/403 au handshake déclenche au plus un renouvellement partagé par jeton
rejeté avant une nouvelle tentative. Un namespace établi avec ce jeton
réinitialise ce compteur, pour qu'une expiration ultérieure puisse renouveler
à nouveau. Les pannes réseau et HTTP 5xx ne renouvellent pas
le jeton : elles signalent `AxencoConnectionError` et permettent la reconnexion
automatique après une coupure. Un refus permanent arrête les
reconnexions et émet `ConnectionChanged(connected=False, error=AxencoAuthError(...))`.
Un HTTP 401 déclenche au plus un renouvellement partagé puis une seule nouvelle
tentative ; un refus de renouvellement exige une nouvelle authentification.

La session aiohttp fournie appartient à l'appelant et n'est jamais fermée par
la bibliothèque. `disconnect_websocket()` arrête aussi les handlers en cours,
la récupération d'inventaire et les tâches de reconnexion ;
`logout()` désabonne et efface le cache même si le serveur refuse la déconnexion.
Socket.IO ≥ 5.12 est requis pour l'API d'arrêt des tâches de reconnexion.
Avec Socket.IO 5.12, chaque refus de namespace peut attendre dix secondes ;
un refus persistant pendant le setup peut donc prendre vingt secondes.
Les méthodes d'action lèvent des exceptions publiques, sans retour booléen.
Les programmes et la réponse intérieure de `get_sub_device_state()` restent des
objets JSON non figés ; ils ne doivent pas être utilisés comme snapshots métier.

`register_listener()` et `register_removal_callback()` sont des adaptateurs
dépréciés désinscriptibles. Le listener reçoit un dictionnaire partiel d'état,
mais ne reçoit plus les états arbitrairement recopiés depuis le parent.
`register_discovery_callback()` est déprécié et fournit un dictionnaire projeté
par appareil (sans JSON brut ni liste ambiguë) ; migrer vers `DeviceAdded`.

```python
from pyaxencoapi import AxencoDevice, AxencoStatePatch

device = AxencoDevice.from_json({
    "_id": "device-1",
    "model": "NTD",
    "connected": True,
    "parents": ",gateway-1,",
    "state": {"currentTemp": 21.5, "targetTemp": 22, "targetMode": 2},
})
patch = AxencoStatePatch.from_json({"currentTemp": None})
updated_state = patch.apply(device.state)
assert updated_state.current_temperature is None
assert updated_state.target_temperature == 22
assert device.preset_modes.reverse[2] == "eco"
```

Les snapshots sont immuables. `_id` devient `id`, les noms de champs d'état
deviennent explicites (`current_temperature`, `target_temperature`,
`override_temperature`, `min_temperature`, `max_temperature`,
`comfort_temperature`, `eco_temperature`, `target_mode`, `change_over_user`,
`device_type`). `parents` est un tuple ordonné issu de la chaîne protocolaire
délimitée par des virgules ; `rfid` reste une chaîne optionnelle.
Les identifiants et modèles non vides sont obligatoires ; les modèles inconnus
restent des chaînes sans capacités inventées.

`from_json()` valide les objets à la frontière protocolaire : pas de conversion
de chaînes numériques ni de booléens en nombres, températures finies et codes
entiers. Les codes entiers inconnus sont conservés pour rester extensible.
Les champs optionnels absents ou nuls valent `None` dans un snapshot (y compris
la connexion physique inconnue). Un état de dispositif absent ou nul produit
un état inconnu. Les clés inconnues sont ignorées et aucun JSON brut n'est
conservé. Les constructeurs typés ne remplacent pas cette validation.
Les schémas des programmes et sous-appareils non documentés ne sont pas figés ici.

Dans `AxencoStatePatch`, **absence** signifie `UNSET` (ne rien modifier), tandis
que **null** signifie `None` (effacer la valeur). `apply()` crée un nouvel état
sans toucher au précédent. Un patch est un objet d'état, pas l'enveloppe Socket.IO.
Un événement incomplet d'inventaire doit être réconcilié, pas converti en
dispositif artificiel.

`AxencoEvent` est l'union de `DeviceUpdated` (identifiant, patch et connexion
physique optionnelle), `DeviceAdded` (dispositif complet), `DeviceRemoved`
(retrait confirmé) et `ConnectionChanged` (transport du client).
Une coupure du transport ne modifie pas la connexion physique d'un appareil et
un patch parent ne doit pas être recopié sur ses enfants.

`AxencoError` est la base de `AxencoAuthError`, `AxencoConnectionError` et
`AxencoResponseError`. Les conversions lèvent `AxencoResponseError` sans inclure
les payloads sensibles ; une erreur sous-jacente éventuelle est conservée comme
cause (`raise ... from ...`).

## Modes et compatibilité historique

`PRESET_MODE_MODELS[model]` et `ModelPresetMap.reverse` sont la référence.
Le code `1` signifie **comfort** pour NTD, **on** pour EWS relais et **cooling**
pour UFH : ne jamais inverser une table globale. Pour EWS, `device_type` égal
à `0` sélectionne `EWS_RELAIS`, `1` sélectionne `EWS_PILOTE` ; absent, nul ou
inconnu ne confère aucune capacité. `device.preset_modes` renvoie une copie.

Les anciennes constantes `PRESET_MODE_MAP`, `PRESET_MODE_SELECT_EXTRAS`,
`PRESET_MODE_MAP_RELAIS`, `REVERSE_PRESET_MODE_MAP_RELAIS`,
`PRESET_MODE_MAP_UFH` et `REVERSE_PRESET_MODE_MAP_UFH` sont accessibles depuis
`pyaxencoapi` et `pyaxencoapi.const`, avec `DeprecationWarning`.
Les tables inverses restent strictement propres à leur famille ; aucune table
inverse globale ambiguë n'est fournie. `Preset` et ses codes restent disponibles.

Par rapport à la version **1.0.7** épinglée par Home Assistant :
- `PRESET_MODE_MAP` conserve exactement ses huit clés de thermostat ; les modes
  relais, UFH et `eco_1`/`eco_2` restent dans leurs tables dédiées. Pour les modèles
  supplémentaires, utiliser directement `PRESET_MODE_MODELS[model]`, pas une
  recherche dans la table historique de thermostat.
- `PRESET_BY_KEY` reste accessible avec `DeprecationWarning` depuis les deux
  modules ; il associe chaque clé unique à son `Preset`, y compris les nouveaux
  modes relais, UFH et `eco_1`/`eco_2`.
- `PRESET_BY_CODE` et `REVERSE_PRESET_MODE_MAP` sont supprimés : leurs tables
  globales inverses seraient ambiguës. Utiliser la propriété `reverse` du modèle.
- `PRESET_MODE_MODELS` passe de `dict[str, list[str]]` à
  `dict[str, ModelPresetMap]` : chaque valeur associe désormais les clés aux codes.
  `list(PRESET_MODE_MODELS[model])` fournit les clés et
  `PRESET_MODE_MODELS[model][key]` fournit le code propre au modèle.

## Développement et validation

```bash
python3 --version  # 3.14+
python3 -m pip install -e '.[dev]'
python3 -m pytest
python3 -m mypy
python3 -m build
python3 scripts/check_distribution.py dist/*.whl dist/*.tar.gz
```

Le contrôle de types couvre toute la bibliothèque et un consommateur des exports.
La wheel et le sdist incluent `py.typed` (PEP 561). Les tests REST et Socket.IO
utilisent des serveurs locaux réels et fonctionnent avec aiohttp 3.14 sans
aioresponses ni plafond de version de développement.
