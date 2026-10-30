# Guide d'utilisation — addon Blender GLBGenerator

Version de l'addon : **v0.4** (mini-jeu : entités et arène, patch 17) — guide du 29/10/2026, Blender **4.5**.

Ce guide est fait pour quelqu'un qui connaît les bases de Blender (sélectionner, déplacer, animer avec des clés) mais rien du
projet. Il dit **quoi faire, où cliquer, et quoi vérifier**. Le pourquoi des règles est dans les spécifications
(`spec-addon-glbgenerator.md`, `spec-extras-blender.md`, `spec-actions-combinees.md`, `spec-mini-jeu.md`) ; tu n'en as pas besoin
pour travailler.

**Conventions.** Les menus de Blender sont écrits en anglais, comme dans Blender par défaut (ex. **Object Properties**) ; les
textes de l'addon sont en français, entre guillemets (ex. « Exporter le GLB »). `Maj` = Shift. « Clic droit > X » = faire un
clic droit puis choisir X dans le menu.

---

## 1. Ce que fait l'addon

Un livre augmenté contient des **scènes 3D** qu'on ouvre en filmant un QR code. Chaque scène est fabriquée en quatre temps :

| Étape | Outil | Ce qu'on y fait |
|---|---|---|
| 1. Animer | **Blender + cet addon** | personnages, animations, boutons, caméra, instants des sons ; export du fichier `.glb` |
| 2. Rédiger | **l'atelier** | nom de la scène, libellés et icônes des boutons, sons, quiz, crédits, cadrage de la caméra |
| 3. Contrôler | `book:check` (ou le **studio**) | vérifie que la scène est complète et cohérente |
| 4. Essayer | le **studio** (« Essayer sur ce PC ») | la scène dans le vrai lecteur |

Le livre peut aussi avoir un **mini-jeu** (récompense de fin de collection) : ses personnages (**entités**) et son décor
(**arène**) sont fabriqués avec le même addon, chacun dans son `.blend` (§17).

L'addon s'occupe **uniquement de l'étape 1**. Il écrit la partie « 3D » de la configuration de la scène dans un objet
spécial, `GLBI_ROOT`, contrôle tout ce qui peut l'être dans Blender, puis exporte le GLB avec les bons réglages et le
**relit** pour vérifier le résultat.

Dans le lecteur, une scène a :

- une animation de **repos** (`idle`), jouée en boucle tant qu'on ne touche à rien ;
- des **boutons** : chaque bouton joue une animation (un **clip**), puis la scène revient au repos ;
- une **caméra** que le lecteur fait tourner autour d'un point (la **cible**), et qui peut suivre un chemin pendant une action.

---

## 2. Vocabulaire

| Mot | Sens dans ce guide |
|---|---|
| **Armature** | le squelette d'un personnage (objet Blender de type Armature). Un personnage = une armature + son maillage. |
| **Maillage** | la « peau » visible du personnage (objet Mesh), déformée par l'armature. |
| **Groupe de sommets** | liste de sommets du maillage liés à un os. **Son nom doit être celui de l'os.** |
| **Action** | une animation enregistrée dans Blender (des clés sur des os ou des objets). |
| **Clip** | une animation **dans le GLB**, jouée par le lecteur. Selon le réglage, un clip vient d'une Action ou d'une piste NLA. |
| **Piste NLA, bande** | dans l'éditeur **Nonlinear Animation** (NLA), une piste est une ligne qui contient une ou plusieurs bandes ; chaque bande est une Action placée dans le temps. |
| **Empty** | objet Blender invisible (un repère). Sert ici de cible et de position de la caméra. |
| **Marqueur** | petit triangle posé sur la Timeline à une image donnée ; sert ici à placer les sons. |
| **`idle`** | le repos : animation en boucle quand aucun bouton n'est actif. |
| **id** | nom technique (scène, bouton, événement) : lettres, chiffres, `_` et `-`, sans espace ni accent. |
| **GLB** | le fichier 3D exporté (`scenes/<id>.glb`), lu par le lecteur. |
| **Livre** | le dossier qui contient `book.json`, les `.blend`, les GLB et les fichiers de l'atelier. |

---

## 3. Installation (une seule fois)

1. **Blender 4.5** est nécessaire (l'addon n'est pas garanti dans une autre version).
2. Récupère le dossier **`glb_generator`** (dépôt `blender-addons`). Il contient des fichiers `.py` et un sous-dossier `schema`.
   Ne renomme pas le dossier et ne le vide pas.
3. Copie le dossier `glb_generator` **en entier** dans le dossier des addons de Blender :
   - Windows : `C:\Users\<toi>\AppData\Roaming\Blender Foundation\Blender\4.5\scripts\addons\`
     (crée le dossier `addons` s'il n'existe pas ; `AppData` est un dossier caché : tape le chemin dans la barre de l'Explorateur) ;
   - macOS : `~/Library/Application Support/Blender/4.5/scripts/addons/` ;
   - Linux : `~/.config/blender/4.5/scripts/addons/`.

   Tu dois obtenir `…\scripts\addons\glb_generator\__init__.py`.
4. Lance Blender : **Edit > Preferences > Add-ons**. Tape `GLB` dans la recherche, coche **GLB Generator**.
   Vérifie aussi que **glTF 2.0 format** (l'exportateur de Blender) est coché : il l'est par défaut.
5. Ferme les préférences. Dans la vue 3D, appuie sur **N** (la souris au-dessus de la vue) : un onglet **BD** apparaît sur
   le bord droit. Clique-le : le panneau **« GLBI — GLB Generator »** s'affiche.

**Mettre à jour l'addon** : remplace le dossier `glb_generator` (ou fais `git pull` si c'est un clone git), puis **relance
Blender**. Pour vérifier la version : depuis la v0.4, le sous-panneau **Scène** commence par le réglage **Type** (Scène,
Entité de jeu, Arène).

**Si l'onglet BD n'apparaît pas** : l'addon n'est pas coché, ou le dossier est mal placé (un dossier `glb_generator` dans un
autre dossier `glb_generator`, par exemple). Regarde aussi **Window > Toggle System Console** (Windows) : une erreur au
chargement y est écrite.

---

## 4. Le livre et ses fichiers

```
<livre>/                       ← le « dossier du livre »
  book.json                    ← liste des scènes du livre (créé avec le livre, pas par l'addon)
  blender/
    raptor.blend               ← un .blend par scène, nommé <id de la scène>.blend
    duo.blend
    jeu/                       ← .blend du mini-jeu (§17)
      dino.blend               ← une entité (joueur ou proie), nommée <id de l'entité>.blend
      arena.blend              ← l'arène
  scenes/
    raptor.glb                 ← écrit par l'addon (bouton « Exporter le GLB »)
    raptor.edit.json           ← écrit par l'atelier (textes, sons…)
  game/
    dino.glb, arena.glb        ← écrits par l'addon (Type Entité de jeu ou Arène)
    game.edit.json             ← écrit par l'atelier (règles, textes, sons du jeu)
  sounds/, icons/ …            ← assets choisis dans l'atelier
```

Règles :

- **Un `.blend` par scène**, rangé dans `<livre>/blender/` et nommé comme la scène (`raptor.blend` pour la scène `raptor`).
- L'addon **trouve le livre tout seul** : il remonte depuis le `.blend` jusqu'au premier dossier qui contient `book.json`.
  C'est pourquoi il faut **enregistrer le `.blend` dans le livre avant d'exporter**.
- Le GLB est toujours écrit dans `<livre>/scenes/<id>.glb`. C'est le seul endroit que le build lit.
- Pour que `book:check` accepte la scène, son id doit aussi figurer dans la liste `scenes` de `book.json`, et l'atelier doit
  avoir créé `scenes/<id>.edit.json`.

---

## 5. Le panneau en un coup d'œil

```
GLBI — GLB Generator
├─ « GLBI_ROOT en place » ✓            [bouton à droite : Créer / réparer GLBI_ROOT]
├─ Livre : D:\…\books\dinosaures       Origine : déduit du .blend
│  Préférence (repli) : …              (grisée quand le livre est trouvé)
├─ Fichier GLB : (vide)                → D:\…\books\dinosaures\scenes\raptor.glb
├─ Scène       Type : [Scène | Entité de jeu | Arène] ; id
├─ Caméra      Cible                    (Type Scène)
├─ Animations  Clips : [Actions | Pistes NLA]   (Type Scène)
│              Repos (idle, en boucle) : Objet, Clip (ou Piste)
│              Actions (boutons) : liste  [+] [−] [▲] [▼]
│                 → pour le bouton sélectionné : id, Objet/Acteur, Clip/Piste, Boucle,
│                   Coupe au noir, Position de la caméra, Événements
├─ Entité de jeu  Rôle, Armature, rôles de clip, Capsule   (Type Entité de jeu, §17)
├─ Arène          état des SPAWN_*, COL_*, FADE_*          (Type Arène, §17)
└─ Rapport     [Vérifier] [Appliquer] [Exporter le GLB] [Corriger les noms]
               liste des messages (40 lignes au plus)
```

- **Fichier GLB** : laisse-le **vide**. L'addon calcule le bon chemin (affiché sur la ligne « → … ») : `scenes/<id>.glb`
  pour une scène, `game/<id>.glb` pour une entité, `game/arena.glb` pour l'arène. Un autre chemin est refusé (contrôle 15).
- **Préférence (repli)** : ne sert que si le `.blend` n'est pas rangé dans un livre (nouveau fichier pas encore enregistré,
  par exemple). Elle se règle aussi dans **Edit > Preferences > Add-ons > GLB Generator** (flèche pour déplier).
- Dans le **Rapport**, les erreurs viennent en premier (icône triangle), puis les avertissements (icône **i**), puis les
  informations (sans icône). Chaque ligne commence par le **numéro du contrôle** : le tableau du §14 dit quoi faire.

---

## 6. Préparer une scène (une fois par scène)

1. **Enregistre le `.blend`** dans `<livre>/blender/<id>.blend` (**File > Save As**). Exemple : `books/dinosaures/blender/raptor.blend`.
   En haut du panneau, tu dois lire « Livre : … » et « Origine : déduit du .blend ».
   - « Dossier du livre non trouvé » : le `.blend` n'est pas sous un dossier qui contient `book.json`.
2. **Images par seconde** : règle-les **avant d'animer**, dans **Output Properties** (icône d'imprimante) **> Format > Frame
   Rate** (24 ou 30 en général). Les instants des sons en dépendent ; les changer après décale tout.
3. **`GLBI_ROOT`** : en haut du panneau, si tu lis « Pas de GLBI_ROOT », clique le **bouton à droite** de cette ligne. Un
   empty `GLBI_ROOT` est créé. **Un seul par `.blend`** ; ne le renomme pas, ne le supprime pas, ne l'anime pas. Le même
   bouton le répare (« GLBI_ROOT à réparer ») s'il a été renommé ou retiré de la scène.
4. **Sous-panneau Scène > id** : le nom technique de la scène, **identique au nom du `.blend`** (sans `.blend`). Il commence par
   une lettre ou un chiffre, puis lettres, chiffres, `_` ou `-`. Exemple : `raptor`.
5. **Noms des objets, Actions et pistes NLA** : lettres, chiffres et `_` **uniquement** (pas de point, d'espace, de tiret,
   d'accent). Blender crée souvent des noms comme `Armature.001` : le bouton **« Corriger les noms »** (Rapport) remplace les
   caractères interdits par `_` sans jamais créer de doublon. Les noms des **os** ne sont pas concernés.
6. **Lumières** : ajoute au moins une lampe **Point**, **Spot** ou **Sun** (**Add > Light**). Les lampes **Area** et le monde
   (World) ne sortent pas dans le GLB ; sans lampe, le lecteur met un éclairage neutre. Si les faces à l'ombre sont trop
   sombres dans le lecteur, ajoute une lampe d'appoint.
7. **Personnages** : chaque maillage animé doit avoir un modificateur **Armature** qui pointe vers son armature, et des
   **groupes de sommets nommés comme les os** (c'est automatique avec **Ctrl+P > With Automatic Weights**). Vérifie dans
   **Object Data Properties** (triangle vert) **> Vertex Groups**.
8. Clique **« Vérifier »** dans le Rapport de temps en temps : les messages te guident.

---

## 7. Choisir la source des clips (Animations > « Clips »)

| Réglage | À choisir quand | Un clip, c'est… |
|---|---|---|
| **Actions** | **un seul** personnage, chaque bouton = une Action simple | une **Action** de l'armature |
| **Pistes NLA** | **deux personnages ou plus** (obligatoire) ; ou un personnage dont les animations enchaînent plusieurs Actions ; ou une scène qui utilise la **position de la caméra** (§10.3) | un **nom de piste NLA**, commun à toutes les armatures et aux empties |

En **Pistes NLA**, un bouton joue un **clip commun** : toutes les pistes du même nom (sur chaque personnage, sur les empties)
forment un seul clip, joué ensemble. C'est ainsi qu'on synchronise un dino qui en poursuit un autre.

**En cas de doute, choisis Pistes NLA** : il fait tout ce que fait Actions, et plus.

---

## 8. Tutoriel A — Scène à un personnage (Clips = Actions)

**Créer les animations**

1. Sélectionne l'armature et passe en **Pose Mode** (Ctrl+Tab).
2. Ouvre un éditeur **Dope Sheet** et choisis le mode **Action Editor** (menu en haut à gauche de l'éditeur).
3. Clique **New** : une Action est créée. Renomme-la (double-clic sur son nom), par exemple `idle`. Clique l'icône **bouclier**
   (Fake User) à côté du nom : l'Action est ainsi conservée même quand elle n'est pas affichée.
4. Anime les os (touche **I** sur les os sélectionnés pour poser des clés). **Commence à l'image 0 ou 1.**
5. Pour l'animation suivante : clique la **croix** à côté du nom de l'Action (elle reste dans le fichier grâce au bouclier),
   puis **New**, renomme (`run`, `jump`…), bouclier, anime.

**Déclarer la scène dans l'addon**

6. Animations > **Clips = Actions**.
7. **Repos (idle, en boucle)** : **Objet** = l'armature ; **Clip** = l'Action de repos (`idle`).
   Le menu Clip ne propose que les Actions qui animent des os de cette armature.
8. **Actions (boutons)** : clique **+** pour chaque bouton. Pour le bouton sélectionné dans la liste :
   - **id** : nom du bouton (`run`, `jump`…). C'est aussi le nom que verra l'atelier ;
   - **Objet** : l'armature ;
   - **Clip** : l'Action jouée ;
   - **Boucle** : coche si l'animation doit tourner en boucle tant qu'on ne clique pas ailleurs ;
   - **Coupe au noir** : voir §11 ;
   - **Position de la caméra** : voir §10.3 (demande Clips = Pistes NLA).

   L'ordre de la liste est l'ordre des boutons : ▲ et ▼ pour le changer.
9. **Vérifier**, puis **Exporter le GLB** (§13).

Seules les Actions déclarées (repos + boutons) partent dans le GLB. Une autre Action de l'armature est signalée
(avertissement 10) et ignorée : c'est normal pour un brouillon.

---

## 9. Tutoriel B — Scène à plusieurs personnages (Clips = Pistes NLA)

### 9.1 Le principe

- Chaque personnage a, dans l'éditeur **Nonlinear Animation** (NLA), **une piste par clip**, et **la même liste de noms de
  piste** que les autres : par exemple `idle`, `chasse`, `chute`, `regard` sur chaque armature.
- À l'export, toutes les pistes `chasse` forment **un seul clip `chasse`**, qui anime tout le monde.
- Une piste peut contenir **plusieurs bandes** à la suite : le chasseur court (bande 1) puis mord (bande 2). Les bandes sont
  jouées **à leur place dans la timeline** : c'est là qu'on règle qui fait quoi, et quand.

### 9.2 Pas à pas

1. **Ouvre l'éditeur NLA** : dans la Timeline (en bas), clique l'icône du type d'éditeur (tout à gauche de son en-tête),
   puis **Animation > Nonlinear Animation**. Agrandis la zone en tirant son bord. Touche **Home** (souris dessus) pour tout
   voir.
2. **Crée les Actions** de chaque personnage dans l'**Action Editor** (comme au §8, étapes 1 à 5), avec des noms distincts :
   `Chasseur_course`, `Chasseur_morsure`, `Proie_mange`, `Proie_fuite`…
3. **Range une Action en bande** : l'Action étant affichée dans l'Action Editor (ou en orange sur la ligne de l'objet dans le
   NLA), clique **Push Down**. Elle devient une bande sur une nouvelle piste.
4. **Renomme la piste** du nom du clip : double-clic sur le nom de la piste dans la colonne de gauche du NLA (`NlaTrack` →
   `chasse`).
   **Jamais de nom par défaut** (`NlaTrack`, `NlaTrack.001`, `[Action Stash]`) : l'exportateur ne les regroupe pas (erreur 2).
5. **Ajoute les bandes suivantes** sur la même piste : sélectionne la piste, place l'image courante où la bande doit
   commencer, **Add > Action** (Maj+A) dans le NLA, choisis l'Action. Déplace une bande avec **G**.
   - Chaque clip **commence à l'image 0 ou 1** (avertissement ou erreur 27 sinon).
   - Les bandes restent en mode **Replace** : sélectionne une bande, **N** (souris sur le NLA), onglet **Strip > Blending**.
   - Pour lire le début et la fin exacts d'une bande : même panneau, **Frame Start** et **Frame End**.
6. **Chaque personnage a une piste de chaque clip**, même s'il ne bouge pas dans ce clip : une bande d'une seule clé de pose
   suffit. Sinon sa pose n'est pas garantie dans le lecteur (avertissement 25).
7. **Panneau** :
   - Animations > **Clips = Pistes NLA** ;
   - **Repos** : Objet = l'acteur principal, **Piste** = `idle` ;
   - pour chaque bouton (**+**) : **id**, **Acteur** (le personnage principal du clip), **Piste** (menu des noms de piste de la
     scène), Boucle, Coupe au noir, Position de la caméra.

### 9.3 Regarder un clip dans Blender

Les pistes se superposent : sans réglage, Blender montre la plus haute et masque les autres.

- Pour voir `chasse` : clique l'**étoile** (Solo) de la piste `chasse` **sur chaque personnage** (et sur les empties animés),
  puis lance la lecture (**barre d'espace**).
- Le solo **n'a aucun effet sur l'export** : l'addon et l'exportateur l'enlèvent pendant l'export, puis le remettent.
- **Ne laisse pas une bande en édition** (Tab dans le NLA, bande verte) au moment d'exporter : c'est l'erreur 4 « bande NLA
  en édition ». Ressors par **Tab**.

### 9.4 Faire disparaître un personnage

Anime son **échelle à 0** dans sa bande. Rien d'autre à déclarer.

---

## 10. Caméra du lecteur

Dans le lecteur, on fait tourner la caméra autour d'un point (la **cible**) et on zoome. La distance, les angles et les limites se
règlent dans l'**atelier** (onglet Caméra). Dans Blender, on règle seulement **où** est ce point, et éventuellement **d'où
regarde la caméra** pendant un bouton.

### 10.1 Cible (sous-panneau Caméra > « Cible »)

1. **Add > Empty > Plain Axes**. Renomme-le (F2), par exemple `start`, et place-le là où la caméra doit regarder
   (le centre de l'action, la tête du personnage…).
2. Sous-panneau **Caméra > Cible** : choisis cet empty. Le menu propose les empties de la scène, sauf `GLBI_ROOT`.

Sans cible, la scène n'a pas de module caméra : le lecteur utilise ses réglages par défaut.

### 10.2 Cible qui suit l'action

Pour que la caméra suive un personnage pendant un bouton, **anime la cible** dans ce clip :

1. Sélectionne l'empty cible. Va à la première image du clip. Dans **Object Properties** (carré orange) **> Transform**,
   **clic droit sur Location > Insert Keyframes**. Va à une autre image, déplace l'empty (**G**), recommence.
2. **Push Down** (NLA, ligne de l'empty) puis renomme la piste **du nom du clip** (`chasse`).

**Une contrainte ne sort pas dans le GLB** (Child Of, Copy Location…) : avertissement 26. Pour la convertir en clés :
sélectionne l'empty, **Object > Animation > Bake Action**, coche **Visual Keying**, valide, puis Push Down et renomme la piste.
Supprime ensuite la contrainte.

### 10.3 Position de la caméra pendant un bouton (nouveau, patch 16)

Pendant un bouton, la caméra peut **se placer sur un second empty animé** et regarder la cible. Pendant ce temps, le lecteur
ne peut ni tourner ni zoomer ; à la fin du bouton, la caméra revient en ½ seconde à la vue d'avant.

Conditions : **Clips = Pistes NLA**, et une **cible** (§10.1). Puis :

1. **Add > Empty > Plain Axes**, renomme-le (ex. `CamRegard`). C'est **un empty à part** : jamais la cible elle-même.
2. Note le début et la fin du clip (NLA : clic sur la bande du clip, **N**, onglet **Strip**, Frame Start / Frame End).
3. Va à l'image de début, place l'empty, **clic droit sur Location > Insert Keyframes**. Va à l'image de fin (et à des images
   intermédiaires si tu veux une courbe), déplace l'empty, recommence.
4. Dans le NLA, ligne de l'empty : **Push Down**, puis renomme la piste **exactement comme le clip** (`regard`).
5. Panneau Animations, bouton concerné : **Position de la caméra** = `CamRegard`. Le menu propose les empties de la scène sauf
   `GLBI_ROOT` et la cible.
6. **Vérifier** : aucune ligne 28, 29 ou 30 ne doit apparaître (voir §14).

Le même empty peut servir à plusieurs boutons s'il a une piste pour chacun de leurs clips. Laisse le champ vide pour un bouton
où la caméra reste libre. Pas de position de caméra sur le repos.

Un empty **parenté** à un objet animé (ex. une caméra « embarquée » sur la tête d'un dino) suit son parent : il n'a pas besoin
de clés propres.

---

## 11. Coupe au noir (case par bouton)

- **Décochée** (par défaut) : le lecteur passe d'une animation à l'autre par un **fondu de poses** de 0,25 s.
- **Cochée** : l'écran passe **au noir** (0,3 s), l'animation change, puis l'image revient (0,3 s), à l'entrée et à la sortie
  du bouton.

Coche-la quand un personnage **commence ou finit loin de sa position de repos** (la proie a fui à 2 m : sans coupe, le fondu
la ferait glisser sur le sol). L'addon le détecte : **avertissement 24** quand un personnage est à plus de 10 % de la taille
de la scène de sa position d'`idle`, à la première ou à la dernière image d'un clip sans coupe au noir.

---

## 12. Événements (sons à un instant précis)

Un événement est un instant d'un bouton où le lecteur jouera un son (la morsure, l'impact d'une chute).

1. Dans la **Timeline**, place l'image courante sur l'instant voulu (l'image du contact).
2. Souris au-dessus de la Timeline, touche **M** : un marqueur `F_<image>` apparaît.
3. Renomme-le par **Marker > Rename Marker** (menu de la Timeline), sous la forme **`<id du bouton>:<id de l'événement>`**,
   par exemple `chasse:morsure`.
   **Attention** : F2 renomme l'**objet actif** si la souris n'est pas exactement sur la zone des marqueurs.
4. Le panneau du bouton liste ses événements : `morsure — image 60 — 2,000 s`.
5. Le **son** de l'événement se choisit ensuite dans l'**atelier**.

Règles :

- un **id d'événement unique** par bouton (erreur 21) : lettres, chiffres, `_`, `-` ;
- l'instant doit être **dans le clip** (erreur 22) ;
- **pas d'événement sur `idle`** ;
- un marqueur sans `:` ou d'un bouton inconnu est **ignoré** et signalé (avertissement 23) : sans gravité pour un marqueur de
  travail.

**Instant** = image / images par seconde, compté depuis l'image 0 de la timeline : à 30 i/s, l'image 60 donne 2,000 s.

---

## 13. Vérifier, appliquer, exporter (sous-panneau Rapport)

| Bouton | Effet |
|---|---|
| **Vérifier** | lance tous les contrôles et remplit le Rapport. Ne modifie rien. |
| **Appliquer** | vérifie, puis écrit la configuration dans `GLBI_ROOT`. **Refusé s'il reste une erreur.** |
| **Exporter le GLB** | applique, exporte avec les réglages imposés, puis **relit** le GLB. |
| **Corriger les noms** | remplace les caractères interdits par `_` dans les noms d'objets, d'Actions et de pistes déclarées. |

**Procédure d'export**

1. **Enregistre** le `.blend` (Ctrl+S).
2. **Vérifier** : corrige toutes les **erreurs** ; lis les avertissements (ils n'empêchent pas l'export, mais signalent
   souvent un défaut visible dans le lecteur).
3. **Exporter le GLB**. Message attendu en bas de Blender : **« GLB exporté et vérifié (n clip(s)) : <chemin> »**.
   - « GLB écrit mais non conforme » : le fichier est écrit mais une relecture a échoué ; lis les lignes **« 0 — GLB exporté :
     … »** du Rapport (§14).
   - Une ligne **19 (information)** peut apparaître : des valeurs de pose ont été ajoutées temporairement pour l'export ; rien
     à faire.
4. Le `.blend` **n'est pas modifié** par l'export (la pose et l'image courante sont remises comme avant).

**Toujours exporter par ce bouton**, jamais par **File > Export > glTF 2.0** : le bouton impose les bons réglages, garde la
pose des personnages et filtre ce qui ne doit pas partir. Un export par le menu avec de mauvais réglages est **interrompu**
(contrôle 9) ; avec de bons réglages il passe, mais sans ces protections.

**Après l'export**

- Contrôle du livre : dans le studio, « book:check » ; ou en ligne de commande, depuis le dossier de GLBInterpreter :
  `npm run book:check -- --source <dossier du livre>`. Attendu : **0 erreur**.
- Textes, icônes, sons, quiz, crédits, cadrage : dans l'**atelier**.
- Essai : studio, **« Essayer sur ce PC »**.

---

## 14. Messages du Rapport : que faire

E = erreur (bloque Appliquer et l'export), A = avertissement (n'empêche rien), I = information.

| N° | Niveau | Message (forme) | Que faire |
|---|---|---|---|
| 0 | E | « GLB exporté : … » (après export) : clips absents ou en trop, configuration différente, lampes, position de caméra absente ou non animée… | lire le détail ; corriger dans Blender et réexporter ; si cela persiste, signaler le message |
| 1 | E | aucun / plusieurs `GLBI_ROOT`, mal nommé, pas un empty, hors de la scène | bouton Créer / réparer en haut du panneau ; supprimer les doublons |
| 2 | E | objet, Action ou piste : « lettres, chiffres et _ uniquement » ; collision après normalisation ; piste au nom par défaut | **Corriger les noms**, ou renommer à la main ; pour une piste, même nom sur chaque personnage |
| 3 | E | objet absent, pas une armature ; cible caméra absente ou pas un empty | choisir le bon objet dans le panneau |
| 4 | E | aucun clip / aucune piste ; Action d'une autre armature ; « n'a pas de piste NLA « x » » ; deux pistes du même nom ; piste sans bande ; bande hors Replace ; bande NLA en édition | corriger le champ Clip ou Piste ; créer la piste sur l'acteur ; repasser les bandes en Replace ; Tab pour sortir de l'édition |
| 8 | E | id de bouton en double | renommer l'un des deux |
| 9 | I / E | I : rappel des réglages imposés. E : fenêtre d'erreur « GLBI, contrôle 9 : export interrompu » lors d'un export par le menu File | exporter par le bouton « Exporter le GLB » |
| 10 | A | Action ou piste non déclarée : non exportée | normal pour un brouillon ; sinon la déclarer comme bouton |
| 11 | A | skin orphelin ; maillage vide ; **aucun groupe de sommets ne porte le nom d'un os** (nouveau, patch 16) | relier le maillage à l'armature (modificateur Armature) ou le supprimer ; **renommer les groupes de sommets comme les os** (sinon le maillage reste immobile dans le lecteur) |
| 13 | E | plusieurs armatures avec Clips = Actions | passer en **Pistes NLA** |
| 15 | E | dossier du livre non trouvé ; fichier de sortie différent de `scenes/<id>.glb` (jeu : `game/<id>.glb`) | ranger le `.blend` dans `<livre>/blender/` (jeu : `<livre>/blender/jeu/`) ; vider le champ Fichier GLB |
| 16 | E / I | couche 3D refusée par le JSON Schema (id mal formé, champ vide, aucun bouton…) ; en I, `$id` du schéma | corriger le champ indiqué ; si « JSON Schema : fichier absent », réinstaller l'addon complet (dossier `schema` compris) ou, côté développeur, lancer `npm run schema:export -- --addon <glb_generator>` dans GLBInterpreter |
| 17 | A | action de `edit.json` absente de Blender | rattacher ou supprimer cette action dans l'atelier, ou recréer le bouton dans Blender |
| 18 | A | lampe Area ou Hemi ignorée ; scène sans lumière ; **entité de jeu** : lampes non exportées | utiliser Point, Spot ou Sun ; pour une entité, l'éclairage vient de l'arène : rien à faire (ou supprimer la lampe) |
| 19 | I / A | I : valeurs de pose non clées enregistrées pour l'export ; A : valeurs ambiguës exportées au repos | I : rien ; A : ajouter une clé de ce canal dans l'Action citée |
| 20 | A | le `.blend` ne s'appelle pas `<id>.blend` ; `.blend` du jeu hors de `blender/jeu/` ; Type Scène dans `blender/jeu/` | **File > Save As** `blender/<id>.blend` (jeu : `blender/jeu/<id>.blend`, `blender/jeu/arena.blend`) ; régler le Type |
| 21 | E | événement en double (marqueurs aux images …) | renommer ou supprimer un marqueur |
| 22 | E | événement hors du clip (images …, secondes …) | déplacer le marqueur dans l'intervalle du clip |
| 23 | A | marqueur ignoré (sans « : », bouton inconnu, `idle`) | renommer en `<bouton>:<événement>`, ou laisser s'il ne sert pas au lecteur |
| 24 | A / I | personnage à x m de sa position d'`idle` à la première / dernière image ; I : vérification sautée (bande en édition) | cocher **Coupe au noir**, ou ramener la pose ; I : sortir de l'édition (Tab) |
| 25 | A | une armature n'a pas de piste du clip | ajouter la piste (une bande de pose suffit) |
| 26 | A | cible caméra à contrainte, non exportée | clés de position dans une piste du nom du clip (§10.2) |
| 27 | E / A | E : clip qui commence avant l'image 0 ; A : clip qui commence tard (pose tenue n s au début) | faire commencer les bandes à l'image 0 ou 1 |
| 28 | E | **position de caméra** (nouveau) : l'objet n'est pas dans la scène ; n'est pas un empty ; sans cible caméra ; identique à la cible | choisir un empty de la scène ; choisir une **cible** (sous-panneau Caméra) ; prendre deux empties différents |
| 29 | A | **position de caméra sans clés de position dans le clip** (nouveau) : « pas de piste x », « piste x sans clés de position », ou en Clips = Actions « non animé dans le clip » → caméra fixe pendant l'action | animer l'empty par des clés de position dans une piste du nom du clip (§10.3) ; en Clips = Actions, passer en Pistes NLA |
| 30 | A | **position de caméra à contrainte**, non exportée | clés de position (Bake Action, Visual Keying), comme pour la cible |
| 31 | E | **entité** : aucune armature, plusieurs armatures, armature non choisie ; id `arena` | une seule armature par `.blend` d'entité, choisie dans le panneau ; `arena` est réservé à l'arène |
| 32 | E | **entité** : rôle de clip obligatoire absent ; Action d'une autre armature | renseigner le rôle (§17.2) ; choisir une Action de l'armature de l'entité |
| 33 | E | **entité** : `COLLIDER` n'est pas un empty, hors de la scène, ou mal nommé (`COLLIDER.001`) | un **empty** nommé exactement `COLLIDER`, dans la scène |
| 34 | E / A | **arène** : pas de `SPAWN_player`, aucun `SPAWN_prey_*`, pas de `COL_sol`, `COL_*` sans maillage ; A : `FADE_*` sans maillage | ajouter les empties et le sol (§17.3) ; un `COL_*` est un objet Mesh avec des faces |
| 35 | E / I | **entité** : capsule non mesurable (aucun maillage, personnage sous l'origine) ; saisie : hauteur < diamètre ; I : capsule mesurée ou saisie | poser les pieds à l'origine ; corriger la saisie |
| 36 | A | capsule de plus de 1,2 m de rayon (budget du jeu) | réduire l'échelle de l'entité, ou saisir une capsule plus petite |
| 37 | A / I | **arène** : collisionneurs `COL_*` de plus de 5 000 triangles ; I : triangles comptés | simplifier les `COL_*`, surtout `COL_sol` |
| 38 | A | **jeu** : texture de plus de 1024 px | réduire l'image à 1024 px |
| 39 | A | **entité** : pieds loin de l'origine ; personnage décalé de l'axe de la capsule | ramener les pieds à l'origine et centrer le personnage, ou poser un empty `COLLIDER` |
| 40 | A | **scène** en Clips = Actions : cible caméra animée, que la caméra ne suivra pas | passer en Pistes NLA et animer la cible dans une piste du nom du clip (§10.2) |

---

## 15. Problèmes fréquents

**Le menu Objet / Clip / Piste / Cible est vide ou ne propose pas mon objet.**
- *Objet* : seules les armatures **de la scène** sont proposées.
- *Clip* (Clips = Actions) : choisis d'abord l'**Objet** ; le menu ne montre que les Actions qui animent des os de cette
  armature.
- *Piste* (Clips = Pistes NLA) : les noms par défaut (`NlaTrack…`) ne sont jamais proposés : renomme la piste.
- *Cible*, *Position de la caméra* : seuls les **empties** de la scène sont proposés (pas `GLBI_ROOT`, et la cible n'est pas
  proposée comme position).

**Mon Action a disparu après avoir rouvert le fichier.** Elle n'était utilisée nulle part et n'avait pas de **Fake User**
(bouclier). Pense au bouclier dès la création (§8, étape 3).

**Un personnage ne bouge pas dans le lecteur alors qu'il bouge dans Blender.** Regarde l'avertissement **11** : ses groupes
de sommets ne portent sans doute pas le nom des os. Sinon, en Pistes NLA, vérifie qu'il a une piste de chaque clip (25) et
que les bandes sont en Replace (4).

**La caméra ne suit pas l'empty pendant un bouton.** Avertissement **29** ou **30**. L'empty doit avoir des **clés de
position** dans une piste **du même nom que le clip**, et la scène doit être en **Pistes NLA**.

**Le son tombe au mauvais moment.** Vérifie les images par seconde (§6, étape 2) et que le marqueur est sur la bonne image ;
le panneau du bouton affiche l'instant calculé.

**« export interrompu » (contrôle 9)** : tu as exporté par le menu **File > Export**. Utilise le bouton « Exporter le GLB ».

**Pointes ou étirements sur les doigts d'un modèle importé (FBX).** L'export par le bouton enregistre les corrections de pose
non clées (ligne 19). Si un avertissement 19 cite des valeurs ambiguës, ajoute une clé de ce canal dans l'Action citée.

**`book:check` : « action « x » absente du GLB ».** L'atelier connaît un bouton `x` que Blender n'exporte pas (bouton
supprimé ou renommé dans Blender). Recrée le bouton dans Blender, ou rattache / supprime l'action dans l'atelier (le
contrôle 17 le signale déjà dans Blender).

**`book:check` : « … sans son (ignoré) ».** Un événement n'a pas encore de son : à choisir dans l'atelier. Ce n'est qu'un
avertissement.

**Jeu : un personnage court de travers ou à reculons.** Il doit regarder vers **−Y** dans Blender (vue de face, touche 1 du
pavé numérique : tu vois son visage).

**Jeu : le personnage flotte ou s'enfonce dans le sol.** Ses pieds ne sont pas à l'origine (avertissement 39) : déplace le
personnage (pas l'origine), ou pose un empty `COLLIDER` au centre de son corps.

---

## 16. Exemple complet : la scène `duo` du livre de test

`books/test-dino/blender/duo.blend`, deux personnages (`Armature_Chasseur`, `Armature_Proie`), **Clips = Pistes NLA** :

- pistes `idle`, `chasse` (deux bandes par personnage : course puis morsure, repas puis fuite), `chute` et `regard` ;
- cible `start`, animée dans une piste `chasse` ;
- empty **`CamRegard`**, animé dans une piste `regard`, choisi comme **Position de la caméra** du bouton `regard` ;
- marqueurs `chasse:morsure` (60), `chasse:souffle` (75), `chasse:fuite` (96) et `chute:impact` (45) ;
- **Coupe au noir** sur `chute`.

À la vérification, il donne deux avertissements 24 sur `chasse`, qui finit loin du repos ; ils disparaissent si l'on coche
Coupe au noir sur `chasse`.

---

## 17. Mini-jeu : entités et arène

Le mini-jeu du livre (gabarit « poursuite » du livre 1 : un dino chasse des proies) se fabrique avec **un `.blend` par
fichier** du jeu, rangé dans `<livre>/blender/jeu/` :

| Fichier du jeu | `.blend` | Type (sous-panneau Scène) |
|---|---|---|
| `game/<id>.glb` (joueur ou proie) | `blender/jeu/<id>.blend` | **Entité de jeu** |
| `game/arena.glb` | `blender/jeu/arena.blend` | **Arène** |

Les règles (durée, nombre de proies, vitesses), les textes et les sons du jeu se règlent dans l'**atelier**
(`game/game.edit.json`). Le gabarit de jeu se choisit dans le **studio** (`book.json`).

### 17.1 Préparer le `.blend`

1. **File > Save As** `<livre>/blender/jeu/<id>.blend` (l'arène : `arena.blend`). « Origine : déduit du .blend » en haut.
2. `GLBI_ROOT` : bouton Créer / réparer, comme pour une scène (§6).
3. Sous-panneau **Scène > Type** : **Entité de jeu** ou **Arène**. Les sous-panneaux Caméra et Animations disparaissent ;
   **Entité de jeu** ou **Arène** apparaît.
4. Entité : **id** = nom du fichier (`dino`, `compso`…) ; jamais `arena`. Arène : l'id est fixé à `arena`.

### 17.2 Entité (joueur ou proie)

Une entité = **un personnage** : une armature et son maillage, animés par des Actions (comme au §8, étapes 1 à 5).

- **Orientation** : le personnage regarde vers **−Y** (vue de face de Blender). **Pieds à l'origine** (sol à Z = 0), corps
  centré sur l'axe vertical.
- **Échelle** : 1 unité Blender = 1 m dans le jeu.
- **Une seule armature** dans le `.blend` (erreur 31).
- **Lampes** : inutiles, elles ne sont pas exportées (le jeu est éclairé par l'arène).

Panneau **Entité de jeu** :

1. **Rôle** : **Joueur** ou **Proie**.
2. **Armature** : l'armature du personnage.
3. **Rôles de clip** : pour chaque rôle, l'Action jouée par le jeu. Obligatoires (marqués `*`) :

   | Rôle | Joueur | Proie | Joué quand… |
   |---|---|---|---|
   | `idle` | * | * | immobile |
   | `walk` | * | * | marche lente (joueur : joystick peu incliné ; proie : errance) |
   | `run` | * | `run` ou `flee` | course (joueur : joystick incliné ; proie : fuite si pas de `flee`) |
   | `attack` | * | | bond du joueur (bouton « Bond », 0,4 s) |
   | `caught` | | * | proie attrapée (1 s, puis elle disparaît) |
   | `flee` | | `run` ou `flee` | fuite de la proie |

   Une même Action peut servir deux rôles. `idle`, `walk`, `run` et `flee` tournent en boucle : fais-les **boucler**
   (dernière pose = première). `attack` et `caught` sont joués une fois.
4. **Capsule de collision** : le jeu fait de chaque personnage une capsule (un cylindre aux bouts arrondis) pour les
   collisions avec le décor.
   - Par défaut, l'addon la **mesure** à la **première image d'`idle`** : rayon = moitié de la **largeur** du personnage (le
     côté étroit, vu de dessus), hauteur = sommet du personnage (au moins le diamètre). La queue et la tête dépassent : c'est
     voulu.
   - **Saisie manuelle** : coche-la pour saisir **Rayon** et **Hauteur** toi-même (la hauteur doit être au moins le double du
     rayon, erreur 35).
   - La mesure s'affiche après **Vérifier** (ligne 35 du Rapport et panneau).
   - Au-delà de **1,2 m de rayon** : avertissement 36 (le jeu n'a pas été mesuré au-delà sur téléphone).
5. **Empty `COLLIDER`** (facultatif) : si le personnage ne peut pas avoir les pieds à l'origine ou n'est pas centré, ajoute un
   **empty** nommé exactement `COLLIDER` au **centre du corps** : la capsule est centrée dessus. Sa hauteur mesurée devient alors
   la hauteur totale du personnage.

Puis **Vérifier** et **Exporter le GLB** (§13) : le fichier est écrit dans `game/<id>.glb`, avec les seules Actions des rôles
(avertissement 10 pour les autres).

### 17.3 Arène

L'arène est le décor du jeu. Rien n'est déclaré dans le panneau : l'addon et le jeu reconnaissent les objets **par leur
nom**.

| Nom | Objet | Rôle dans le jeu |
|---|---|---|
| `SPAWN_player` | empty | point de départ du joueur (orientation : note ci-dessous) |
| `SPAWN_prey_1`, `SPAWN_prey_2`… | empties | points d'apparition des proies (autant que de proies en même temps, sinon elles apparaissent au même endroit) |
| `COL_sol` | maillage | **sol** des collisions (obligatoire) ; sa boîte englobante donne les **limites de l'aire de jeu** |
| `COL_…` (`COL_mur`, `COL_arbre`…) | maillages | obstacles ; **jamais dessinés** dans le jeu |
| `FADE_…` | maillages | décor qui devient **transparent** quand il passe entre la caméra et le joueur (arbres, toits) |
| autres objets | maillages, lampes | décor visible (le sol visible est un objet à part, par exemple `Sol`) |

Note sur `SPAWN_player` : au départ, le joueur regarde selon l'axe **−Y local** de l'empty (comme un personnage, §17.2) ; un
empty sans rotation le fait partir face à la caméra du jeu.

Règles de fabrication :

- **`COL_sol` simple** : **peu de triangles, plat là où c'est possible** (le sol pèse environ un tiers du coût de la physique
  du jeu sur téléphone). Un plan de 2 triangles suffit pour un terrain plat ; s'il est vallonné, un maillage bas-poly à part,
  qui suit grossièrement le sol visible.
- Les `COL_*` sont des **formes simples** (boîtes, cylindres bas-poly) posées sur les obstacles visibles : **5 000 triangles
  au plus** pour tous les `COL_*` ensemble (avertissement 37 ; le panneau Arène affiche le compte).
- Les modificateurs ne sont **pas appliqués** à l'export : applique-les (Ctrl+A > Apply) sur les `COL_*` avant d'exporter.
- **Lampes** : l'éclairage du jeu vient de l'arène (Point, Spot ou Sun ; sans lampe, éclairage de secours).
- Les animations de l'arène ne sont pas exportées (le jeu n'en joue pas).
- Textures : **1024 px au plus** (avertissement 38), pour l'arène comme pour les entités.

Panneau **Arène** : état de `SPAWN_player`, nombre de `SPAWN_prey_*`, `COL_*` et leurs triangles, `COL_sol`, `FADE_*`.

### 17.4 Contrôler et essayer

- `npm run book:check -- --source <livre>` (ou le studio) : il contrôle aussi le jeu (`game/`), et fait foi.
- Essai sur le PC, depuis GLBInterpreter : `npm run dev:book -- --source <livre>` puis `npm run dev`, page `#/dev/jeu`.

---

## 18. Aide-mémoire avant chaque export

- [ ] `.blend` enregistré dans `<livre>/blender/<id>.blend` ; « Origine : déduit du .blend »
- [ ] `GLBI_ROOT en place`
- [ ] id de la scène = nom du `.blend`
- [ ] au moins une lampe Point, Spot ou Sun
- [ ] repos et boutons déclarés ; en Pistes NLA, une piste de chaque clip sur chaque personnage, aucun nom `NlaTrack`
- [ ] aucune bande en édition (Tab)
- [ ] caméra : cible choisie ; empties animés par des **clés** (pas de contrainte), dans une piste du nom du clip
- [ ] **Vérifier** : aucune erreur, avertissements lus
- [ ] **Exporter le GLB** : « GLB exporté et vérifié »
- [ ] `book:check` sans erreur, puis atelier et essai dans le studio
- [ ] **jeu** : `.blend` dans `blender/jeu/`, Type réglé ; entité : une armature, rôles `*` renseignés, regard vers −Y, pieds à
      l'origine ; arène : `SPAWN_player`, `SPAWN_prey_*`, `COL_sol` simple
