# Diretrizes Obrigatórias — Projeto JARVIS

## Atualização Automática do Git a Cada Melhoria
Sempre que qualquer melhoria, correção ou nova funcionalidade for implementada e validada no projeto **JARVIS** (`PROJETO-JARVIS` / pasta `Jarvis- Aula 01`):

1. **Garantir a branch oficial (`main`):**
   - Certifique-se de operar na branch `main` rastreando `origin/main` (`https://github.com/falecomrafaelmuniz-cloud/PROJETO-JARVIS.git`).
   - Antes de enviar, sincronize eventuais novidades remotas com `git pull --rebase --autostash origin main`.
2. **Proteger dados locais e segredos:**
   - Revise `git status` para nunca versionar `.env`, `credentials.json`, `token.json`, `Memorias_do_Jarvis.txt` ou bancos locais em `memoria_jarvis/`.
   - Se adicionar bibliotecas novas em Python ou Node.js, atualize `backend/requirements.txt` ou `frontend/package.json` + `pnpm-lock.yaml` para que os outros PCs instalem automaticamente na inicialização.
3. **Commit e Push automáticos sem esperar pedido extra:**
   - Ao finalizar e validar a melhoria, execute `git add`, `git commit -m "<tipo>: <descrição clara>"` e `git push origin main` automaticamente.
   - Informe ao usuário o commit enviado e o número da nova versão (`v3.0.<contagem_de_commits>`).
