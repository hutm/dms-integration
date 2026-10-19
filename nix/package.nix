{ pkgs }:
let python = pkgs.python3.withPackages (p: [ p.google-auth-oauthlib ]);
in pkgs.writeShellApplication {
  name = "dank-todo-google-sync";
  runtimeInputs = [ pkgs.dms-shell pkgs.xdg-utils ];
  text = ''
    exec ${python}/bin/python3 ${../google-tasks-sync.py} "$@"
  '';
}
