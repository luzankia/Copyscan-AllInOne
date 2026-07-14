import os
import sys
import zipfile
import argparse
import subprocess

def has_single_root_folder(zip_ref):
    """
    Vérifie si le contenu de l'archive ZIP possède un unique dossier à sa racine.
    """
    namelist = zip_ref.namelist()
    
    # On récupère le premier élément de chaque chemin (le nom à la racine)
    root_items = set(path.split('/')[0] for path in namelist if path.strip('/'))
    
    # S'il n'y a qu'un seul élément à la racine, on vérifie que c'est bien un dossier
    if len(root_items) == 1:
        root_item = list(root_items)[0]
        for path in namelist:
            # Si un fichier se trouve à l'intérieur de cet élément, c'est un dossier
            if '/' in path and path.startswith(root_item + '/'):
                return True
                
    return False

def main():
    # 1. Gestion des arguments en ligne de commande
    parser = argparse.ArgumentParser(description="Extrait des archives .cbz de manière intelligente.")
    parser.add_argument("-d", "--dir", type=str, help="Le chemin du dossier contenant les archives .cbz")
    args = parser.parse_args()

    target_dir = args.dir

    # 2. Demande interactive si l'argument n'est pas fourni
    if not target_dir:
        target_dir = input("Veuillez entrer le chemin du dossier contenant les .cbz : ").strip()

    # Nettoyage et vérification du chemin
    target_dir = os.path.abspath(target_dir)
    if not os.path.isdir(target_dir):
        print(f"Erreur : Le dossier spécifié est introuvable -> {target_dir}")
        sys.exit(1)

    # 3. Recherche des fichiers .cbz
    cbz_files = [f for f in os.listdir(target_dir) if f.lower().endswith('.cbz')]
    
    if not cbz_files:
        print("Aucun fichier .cbz trouvé dans le dossier.")
        sys.exit(0)

    print(f"Trouvé {len(cbz_files)} fichier(s) .cbz. Début du traitement...")

    all_successful = True
    processed_paths = []

    # 4. Traitement des archives
    for cbz_file in cbz_files:
        cbz_path = os.path.join(target_dir, cbz_file)
        
        try:
            with zipfile.ZipFile(cbz_path, 'r') as zip_ref:
                if has_single_root_folder(zip_ref):
                    print(f"[Dossier Unique] Extraction de : {cbz_file}")
                    # Extrait directement dans target_dir (le dossier racine interne fera le reste)
                    zip_ref.extractall(path=target_dir)
                else:
                    print(f"[Fichiers Multiples] Extraction de : {cbz_file}")
                    # Création d'un dossier du même nom que l'archive
                    folder_name = os.path.splitext(cbz_file)[0]
                    extract_path = os.path.join(target_dir, folder_name)
                    os.makedirs(extract_path, exist_ok=True)
                    zip_ref.extractall(path=extract_path)
                    
            processed_paths.append(cbz_path)
            
        except Exception as e:
            print(f"Erreur lors de l'extraction de {cbz_file} : {e}")
            all_successful = False

    # 5. Suppression des archives et lancement du script secondaire
    if all_successful:
        print("\nExtraction terminée avec succès. Nettoyage des archives...")
        for cbz_path in processed_paths:
            try:
                os.remove(cbz_path)
                print(f"  -> Supprimé : {os.path.basename(cbz_path)}")
            except Exception as e:
                print(f"Erreur lors de la suppression de {cbz_path} : {e}")
        
        print("\nLancement de main.py...")
        # Construction de la commande à exécuter
        cmd = [
            sys.executable, "main.py", 
            "--root-dir", target_dir, 
            "--skip-step", "1", "3", "4", "5", "5.1", "6", "7", "8"
        ]
        
        try:
            # Exécution de main.py (laisse la main à l'autre script et affiche sa sortie)
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"\nErreur : Le script main.py s'est terminé avec le code d'erreur {e.returncode}")
        except FileNotFoundError:
            print("\nErreur : Impossible de trouver main.py dans le répertoire courant.")
    else:
        print("\nDes erreurs sont survenues pendant l'extraction. Par sécurité, les .cbz originaux n'ont pas été supprimés et main.py ne sera pas exécuté.")

if __name__ == "__main__":
    main()