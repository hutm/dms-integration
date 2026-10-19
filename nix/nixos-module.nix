{ config, lib, pkgs, ... }:
let
  cfg = config.services.dankTodoGoogleTasks;
  sync = pkgs.callPackage ./package.nix { };
in {
  options.services.dankTodoGoogleTasks = {
    enable = lib.mkEnableOption "personal Dank Todo synchronization with Google Tasks";
    user = lib.mkOption {
      type = lib.types.str;
      description = "Desktop account managed by Home Manager that runs the sync.";
      example = "alice";
    };
  };

  config = lib.mkIf cfg.enable {
    programs.dms-shell.plugins.dankTodo = {
      enable = true;
      src = pkgs.applyPatches {
        name = "dank-todo-google-tasks";
        src = pkgs.fetchFromGitHub {
          owner = "deepu105";
          repo = "dms-dank-todo";
          rev = "ff1b82c3061fce3c32c8f65792fa81b928ab35d1"; # v1.2.0
          hash = "sha256-zX9rtJwq5KyXHG55bQXbV7osAMHTkL9l7qSRqEgh6kI=";
        };
        patches = [ ../dank-todo-sync.patch ];
      };
    };

    home-manager.users.${cfg.user} = { lib, ... }: {
      home.packages = [ sync ];
      home.file.".local/bin/dank-todo-google-sync".source = "${sync}/bin/dank-todo-google-sync";
      systemd.user.services.dank-todo-google-sync = {
        Unit = {
          Description = "Synchronize Dank Todo with Google Tasks";
          After = [ "dms.service" ];
          PartOf = [ "graphical-session.target" ];
          ConditionPathExists = "%h/.local/state/dank-todo-google-tasks/token.json";
        };
        Service = {
          Type = "oneshot";
          ExecStart = "${sync}/bin/dank-todo-google-sync sync";
          TimeoutStartSec = 180;
          UMask = "0077";
          NoNewPrivileges = true;
          PrivateTmp = true;
        };
      };
      systemd.user.timers.dank-todo-google-sync = {
        Unit = {
          Description = "Sync Dank Todo every 30 seconds";
          PartOf = [ "graphical-session.target" ];
        };
        Timer = {
          OnCalendar = "*-*-* *:*:00,30";
          AccuracySec = "1s";
        };
        Install.WantedBy = [ "graphical-session.target" ];
      };
      home.activation.dankTodo = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        ${pkgs.python3}/bin/python3 ${../enable-plugin.py}
      '';
    };
  };
}
