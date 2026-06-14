import argparse
import yaml
import sys
import time
from pathlib import Path

from utils import setup_environment, check_prerequisites, console
import workflow

def load_config(config_path="config.yaml"):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        console.print(f"[bold red]Failed to load config.yaml: {e}[/bold red]")
        sys.exit(1)

def parse_cli_args(config):
    parser = argparse.ArgumentParser(description="Image Processing Workflow CLI")
    parser.add_argument("--root-dir", type=str, help="Override root_dir")
    parser.add_argument("--dest-dir", type=str, help="Override dest_dir")
    parser.add_argument("--skip-step", type=int, nargs='+', help="Steps to skip (1-8)", default=[])
    
    args = parser.parse_args()
    
    if args.root_dir: config['root_dir'] = args.root_dir
    if args.dest_dir: config['dest_dir'] = args.dest_dir
    
    for step in args.skip_step:
        step_key = f"step_{step}"
        if step_key in config['steps_active']:
            config['steps_active'][step_key] = False

    return config

def execute_workflow(config):
    # Ajoutez la nouvelle clé 'step_5_1' dans le mapping
    steps_map = {
        'step_1': workflow.step_1_integrity,
        'step_2': workflow.step_2_web_ui,
        'step_3': workflow.step_3_regex_clean,
        'step_4': workflow.step_4_delete_empty,
        'step_5': workflow.step_5_rename_leaf,
        'step_5_1': workflow.step_5_1_clean_hash_suffix, # <-- Ajout ici
        'step_6': workflow.step_6_compress,
        'step_7': workflow.step_7_csv_rename,
        'step_8': workflow.step_8_final_move
    }

    console.print("[bold magenta]=== Starting Image Processing Workflow ===[/bold magenta]")
    
    # On définit explicitement la liste ordonnée des clés d'étapes à exécuter
    ordered_steps = [
        'step_1', 'step_2', 'step_3', 'step_4', 
        'step_5', 'step_5_1', 'step_6', 'step_7', 'step_8'
    ]
    
    for step_key in ordered_steps:
        # Par défaut, si l'étape 5.1 n'est pas mentionnée dans config.yaml, 
        # on s'aligne sur le statut d'activation de la step_5 générale
        is_active = config['steps_active'].get(
            step_key, 
            config['steps_active'].get('step_5', True) if step_key == 'step_5_1' else False
        )
        
        if not is_active:
            console.print(f"[yellow]Skipping {step_key.replace('_', ' ').title()} (Disabled)[/yellow]")
            continue
            
        step_func = steps_map[step_key]
        status = step_func(config)
        
        if status == "quit":
            console.print("[bold red]Workflow aborted by user.[/bold red]")
            sys.exit(0)
            
        # Post-step successful sleep (sauf pour l'interface web)
        if step_key != 'step_2':
            sleep_t = config['sleep_time']
            console.print(f"[dim]Pausing for {sleep_t} seconds...[/dim]")
            time.sleep(sleep_t)

if __name__ == "__main__":
    # 1. Chargement de la configuration initiale
    config = load_config("config.yaml")
    
    # 2. Application des arguments de la ligne de commande si présents
    config = parse_cli_args(config)
    
    # 3. Initialisation de l'environnement (Logs, encodage)
    setup_environment(config['log_path'])
    
    # 4. Vérification des prérequis système (ImageMagick, 7-Zip)
    check_prerequisites()
    
    # 5. Lancement du workflow
    execute_workflow(config)

    # 6. PAUSE FINALE (Ajout ici)
    console.print("\n[bold magenta]===================================================[/bold magenta]")
    console.print("[bold green]✓ Le workflow est terminé avec succès ![/bold green]")
    console.print("[dim]Appuyez sur ENTRÉE pour fermer cette fenêtre...[/dim]")
    input()