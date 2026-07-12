import argparse
import yaml
import sys
import time
from pathlib import Path

from utils import setup_environment, check_prerequisites, console
import workflow

# Étapes valides pouvant être passées à --skip-step (inclut la sous-étape 5.1)
VALID_STEP_TOKENS = ["1", "2", "3", "4", "5", "5.1", "6", "7", "8"]

# Clés obligatoires attendues dans config.yaml, avec leur type Python attendu
REQUIRED_CONFIG_KEYS = {
    "root_dir": str,
    "dest_dir": str,
    "csv_1_path": str,
    "csv_2_path": str,
    "log_path": str,
    "supported_extensions": list,
    "sleep_time": (int, float),
    "im_timeout": (int, float),
    "web_port": int,
    "thumb_size": str,
    "steps_active": dict,
    "delete_regex": list,
    "rename_regex": list,
}

def load_config(config_path="config.yaml"):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        console.print(f"[bold red]Config file not found: {config_path}[/bold red]")
        console.print("[yellow]Copy 'config.example.yaml' to 'config.yaml' and adjust it to your setup.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Failed to load {config_path}: {e}[/bold red]")
        sys.exit(1)

    validate_config(config, config_path)
    return config

def validate_config(config, config_path):
    """Vérifie que toutes les clés requises sont présentes et correctement typées."""
    if not isinstance(config, dict):
        console.print(f"[bold red]Invalid config: {config_path} does not contain a valid YAML mapping.[/bold red]")
        sys.exit(1)

    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in config]
    if missing:
        console.print(f"[bold red]Missing required key(s) in {config_path}: {', '.join(missing)}[/bold red]")
        console.print("[yellow]See 'config.example.yaml' for the full expected structure.[/yellow]")
        sys.exit(1)

    wrong_type = []
    for key, expected_type in REQUIRED_CONFIG_KEYS.items():
        if not isinstance(config[key], expected_type):
            wrong_type.append(f"'{key}' (expected {expected_type})")
    if wrong_type:
        console.print(f"[bold red]Invalid type for key(s) in {config_path}: {', '.join(wrong_type)}[/bold red]")
        sys.exit(1)

def parse_cli_args(config):
    parser = argparse.ArgumentParser(description="Image Processing Workflow CLI")
    parser.add_argument("--root-dir", type=str, help="Override root_dir")
    parser.add_argument("--dest-dir", type=str, help="Override dest_dir")
    parser.add_argument(
        "--skip-step",
        type=str,
        nargs='+',
        help="Steps to skip, e.g. '2 5.1 6' (valid values: 1, 2, 3, 4, 5, 5.1, 6, 7, 8)",
        default=[]
    )

    args = parser.parse_args()

    if args.root_dir: config['root_dir'] = args.root_dir
    if args.dest_dir: config['dest_dir'] = args.dest_dir

    invalid = [s for s in args.skip_step if s not in VALID_STEP_TOKENS]
    if invalid:
        console.print(f"[bold red]Invalid --skip-step value(s): {', '.join(invalid)}[/bold red]")
        console.print(f"[yellow]Valid values are: {', '.join(VALID_STEP_TOKENS)}[/yellow]")
        sys.exit(1)

    for step in args.skip_step:
        step_key = f"step_{step.replace('.', '_')}"
        # step_5_1 peut être absent de steps_active si non défini explicitement
        # dans config.yaml : on l'ajoute quand même pour que --skip-step 5.1
        # soit toujours respecté par execute_workflow.
        config['steps_active'][step_key] = False

    return config

def execute_workflow(config):
    steps_map = {
        'step_1': workflow.step_1_integrity,
        'step_2': workflow.step_2_web_ui,
        'step_3': workflow.step_3_regex_clean,
        'step_4': workflow.step_4_delete_empty,
        'step_5': workflow.step_5_rename_leaf,
        'step_5_1': workflow.step_5_1_clean_hash_suffix,
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
    console.print("[bold green]✓ The workflow has been completed successfully![/bold green]")
    console.print("[dim]Press ENTER to close this window...[/dim]")
    input()