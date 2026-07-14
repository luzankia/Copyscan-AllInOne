import argparse
import yaml
import sys
import time
from pathlib import Path

from utils import setup_environment, check_prerequisites, console
import workflow

# Valid step tokens accepted by --skip-step (includes sub-step 5.1)
VALID_STEP_TOKENS = ["1", "2", "3", "4", "5", "5.1", "6", "7", "8"]

# Required keys expected in config.yaml, with their expected Python type
REQUIRED_CONFIG_KEYS = {
    "root_dir": str,
    "dest_dir": str,
    "csv_1_path": str,
    "csv_2_path": str,
    "log_path": str,
    "log_enabled": bool,
    "supported_extensions": list,
    "sleep_time": (int, float),
    "im_timeout": (int, float),
    "web_port": int,
    "thumb_size": str,
    "steps_active": dict,
    "mask_security_popups": bool,
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
    """Check that all required keys are present and correctly typed."""
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
    parser.add_argument("--log-path", type=str, help="Override log_path (log file destination)")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Leaf folders sit directly under root_dir instead of the standard "
             "root_dir/Parent1/Parent2/Leaf structure."
    )
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
    if args.log_path: config['log_path'] = args.log_path
    if args.local: config['local_mode'] = True

    invalid = [s for s in args.skip_step if s not in VALID_STEP_TOKENS]
    if invalid:
        console.print(f"[bold red]Invalid --skip-step value(s): {', '.join(invalid)}[/bold red]")
        console.print(f"[yellow]Valid values are: {', '.join(VALID_STEP_TOKENS)}[/yellow]")
        sys.exit(1)

    for step in args.skip_step:
        step_key = f"step_{step.replace('.', '_')}"
        # step_5_1 may be absent from steps_active if not explicitly defined
        # in config.yaml: we add it anyway so --skip-step 5.1 is always
        # honored by execute_workflow.
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
    
    # Explicit ordered list of step keys to execute
    ordered_steps = [
        'step_1', 'step_2', 'step_3', 'step_4', 
        'step_5', 'step_5_1', 'step_6', 'step_7', 'step_8'
    ]
    
    for step_key in ordered_steps:
        # By default, if step 5.1 is not mentioned in config.yaml,
        # align it with the general step_5 activation status
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
            
        # Post-step successful sleep (except for the web interface)
        if step_key != 'step_2':
            sleep_t = config['sleep_time']
            console.print(f"[dim]Pausing for {sleep_t} seconds...[/dim]")
            time.sleep(sleep_t)

if __name__ == "__main__":
    # 1. Load initial configuration
    config = load_config("config.yaml")
    
    # 2. Apply command line arguments if present
    config = parse_cli_args(config)
    
    # 3. Initialize the environment (logging, encoding)
    setup_environment(config['log_path'], config['log_enabled'])
    
    # 4. Check system prerequisites (ImageMagick, 7-Zip)
    check_prerequisites(config)
    
    # 5. Run the workflow
    execute_workflow(config)

    # 6. FINAL PAUSE
    console.print("\n[bold magenta]===================================================[/bold magenta]")
    console.print("[bold green]✓ The workflow has been completed successfully![/bold green]")
    console.print("[dim]Press ENTER to close this window...[/dim]")
    input()