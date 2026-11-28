import { useCallback, useEffect, useState } from 'react';
import { ArrowUpCircle, Check, Power, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface WelcomeViewProps {
  startButtonText: string;
  onStartCall: () => void;
  initialShutDown?: boolean;
}

export const WelcomeView = ({
  startButtonText,
  onStartCall,
  initialShutDown = false,
  ref,
}: React.ComponentProps<'div'> & WelcomeViewProps) => {
  const [isShutDown, setIsShutDown] = useState(
    () =>
      initialShutDown ||
      (typeof window !== 'undefined' &&
        Boolean((window as unknown as { __jarvisShutDown?: boolean }).__jarvisShutDown))
  );

  const [version, setVersion] = useState<string>('v3.0.90');
  const [remoteVersion, setRemoteVersion] = useState<string>('');
  const [updateAvailable, setUpdateAvailable] = useState<boolean>(false);
  const [isChecking, setIsChecking] = useState<boolean>(false);
  const [updateState, setUpdateState] = useState<'idle' | 'updating' | 'success' | 'error'>('idle');
  const [updateMessage, setUpdateMessage] = useState<string | null>(null);

  // Checagem silenciosa e automática em segundo plano
  const checkUpdateSilently = useCallback(async () => {
    setIsChecking(true);
    try {
      const res = await fetch('/api/update', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data.version) {
          setVersion(data.version);
        }
        if (data.updateAvailable) {
          setUpdateAvailable(true);
          setRemoteVersion(data.remoteVersion || '');
        } else {
          setUpdateAvailable(false);
        }
      }
    } catch {
      // Falha silenciosa sem interromper o usuário
    } finally {
      setIsChecking(false);
    }
  }, []);

  useEffect(() => {
    void checkUpdateSilently();
  }, [checkUpdateSilently]);

  const waitForRestartAndReload = async (isElectron?: boolean) => {
    if (isElectron) {
      // No Electron Desktop, o script restart_jarvis_update.ps1 fecha e reabre a janela automaticamente
      return;
    }
    await new Promise((r) => setTimeout(r, 3500));
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      try {
        const check = await fetch('/api/connection-details', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
          cache: 'no-store',
        });
        if (check.ok) {
          window.location.reload();
          return;
        }
      } catch {
        // Aguardando servidor web reiniciar com código limpo
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    window.location.reload();
  };

  const handleUpdateJarvis = async () => {
    if (updateState === 'updating') return;
    setUpdateState('updating');
    setUpdateMessage('Sincronizando tudo do GitHub, instalando pacotes e limpando cache...');

    try {
      const response = await fetch('/api/update', { method: 'POST' });
      const data = await response.json();

      if (!response.ok || data.status === 'error') {
        throw new Error(data.message || 'Falha ao atualizar o Jarvis.');
      }

      setUpdateState('success');
      if (data.version) setVersion(data.version);
      setUpdateAvailable(false);
      setUpdateMessage(data.message || 'Jarvis atualizado com sucesso! Reiniciando...');

      void waitForRestartAndReload(Boolean(data.isElectron));
    } catch (error) {
      setUpdateState('error');
      setUpdateMessage(
        error instanceof Error ? error.message : 'Erro ao buscar atualizações do Jarvis.'
      );
      setTimeout(() => {
        setUpdateState('idle');
      }, 5000);
    }
  };

  const handleShutdownSystem = async () => {
    setIsShutDown(true);
    try {
      await fetch('/api/shutdown', { method: 'POST' });
    } catch {
      // Servidor já encerrou
    }
    setTimeout(() => {
      window.close();
    }, 600);
  };

  if (isShutDown) {
    return (
      <div ref={ref}>
        <section className="bg-background flex flex-col items-center justify-center px-4 text-center">
          <Power className="mb-4 size-12 text-red-400/80" />
          <p className="text-foreground max-w-md pt-1 text-base font-semibold">
            Sistemas do Jarvis encerrados com segurança
          </p>
          <p className="text-muted-foreground mt-2 max-w-md text-xs leading-5">
            Todos os processos em segundo plano (Python, LiveKit e servidor web) foram fechados. Você
            já pode fechar esta aba do navegador.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div ref={ref}>
      <section className="bg-black flex h-svh w-svw flex-col items-center justify-center text-center">
        <div className="mb-6 flex flex-col items-center">
          <div className="relative mb-3 flex size-20 items-center justify-center rounded-3xl border border-[#ff5500]/40 bg-[#ff5500]/10 shadow-[0_0_35px_rgba(255,85,0,0.35)] backdrop-blur-xl">
            <span className="size-4 rounded-full bg-[#ff5500] animate-pulse shadow-[0_0_15px_#ff5500]" />
          </div>
          <h1 className="font-mono text-2xl font-bold tracking-[0.3em] uppercase text-white drop-shadow-[0_0_12px_rgba(255,85,0,0.5)]">
            JARVIS <span className="text-[#ff5500]">SYSTEM</span>
          </h1>
          <p className="mt-1 font-serif italic text-xs tracking-wider text-orange-200/70">
            Sistema Neural de Inteligência e Automação Desktop
          </p>
        </div>

        {/* Botão Principal de Iniciar */}
        <Button
          size="lg"
          onClick={onStartCall}
          disabled={updateState === 'updating'}
          className="mt-2 w-72 h-12 rounded-2xl bg-gradient-to-r from-[#ff6b00] to-[#ff4500] font-mono text-xs font-bold tracking-widest text-white uppercase shadow-[0_0_30px_rgba(255,85,0,0.45)] hover:from-[#ff7a1a] hover:to-[#ff5500] hover:shadow-[0_0_40px_rgba(255,85,0,0.65)] transition-all active:scale-[0.98] border border-orange-400/30"
        >
          {startButtonText === 'Iniciar Chamada' ? 'INICIAR NEURAL LINK' : startButtonText}
        </Button>

        {/* Indicador Discreto de Versão Progressiva e Status de Atualização */}
        <div className="mt-3.5 flex flex-col items-center justify-center min-h-[30px]">
          {updateState === 'updating' ? (
            <div className="flex items-center justify-center gap-2 font-mono text-[11px] text-orange-300">
              <RefreshCw className="size-3 animate-spin text-[#ff5500]" />
              <span>
                {updateMessage || `Sincronizando atualização (${remoteVersion || 'GitHub'})...`}
              </span>
            </div>
          ) : updateState === 'success' ? (
            <div className="flex items-center justify-center gap-1.5 font-mono text-[11px] text-emerald-400">
              <Check className="size-3 text-emerald-400" />
              <span>{updateMessage || 'Sistema atualizado! Reiniciando todos os serviços...'}</span>
            </div>
          ) : updateAvailable ? (
            <div className="flex items-center justify-center gap-1.5 font-mono text-[11px]">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-amber-300 shadow-[0_0_12px_rgba(245,158,11,0.2)]">
                <span className="size-2 rounded-full bg-amber-400 animate-pulse" />
                <span className="font-semibold text-white/90">{version}</span>
                <span className="text-amber-400/60">→</span>
                <span className="text-amber-200">{remoteVersion} disponível</span>
                <button
                  type="button"
                  onClick={handleUpdateJarvis}
                  className="ml-1 inline-flex items-center gap-1 rounded bg-amber-500/30 hover:bg-amber-500/50 px-2 py-0.5 text-[10px] font-bold text-amber-100 transition-colors cursor-pointer uppercase tracking-wider"
                >
                  <ArrowUpCircle className="size-3 text-amber-200" />
                  Atualizar Tudo
                </button>
              </span>
            </div>
          ) : (
            <div className="flex items-center justify-center gap-2 font-mono text-[11px] text-orange-200/60 select-none">
              <span className="font-semibold text-orange-400/90">{version}</span>
              <span className="text-orange-500/30">·</span>
              <span className="inline-flex items-center gap-1.5 text-[10px] tracking-wider text-emerald-400/80">
                <span className="size-1.5 rounded-full bg-emerald-400/90 shadow-[0_0_6px_#10b981]" />
                SISTEMA ATUALIZADO
              </span>
              <button
                type="button"
                onClick={handleUpdateJarvis}
                title="Forçar sincronização completa com o GitHub (arquivos, pacotes e cache)"
                disabled={isChecking}
                className="inline-flex items-center gap-1 rounded border border-orange-400/25 bg-orange-500/10 hover:bg-orange-500/25 hover:text-orange-200 text-orange-300/80 transition-colors px-1.5 py-0.5 text-[10px] cursor-pointer ml-0.5"
              >
                <RefreshCw className={`size-2.5 ${isChecking ? 'animate-spin text-orange-400' : ''}`} />
                <span>Atualizar</span>
              </button>
            </div>
          )}

          {updateMessage && updateState === 'error' && (
            <p className="mt-2 max-w-xs text-xs font-medium text-red-400">
              {updateMessage}
            </p>
          )}
        </div>

        {/* Botão Secundário Sutil: Encerrar Sistemas */}
        <Button
          variant="outline"
          size="sm"
          onClick={handleShutdownSystem}
          disabled={updateState === 'updating'}
          className="mt-4 w-72 rounded-2xl border-red-500/20 bg-red-500/5 font-mono text-[11px] font-semibold tracking-wider text-red-400/80 uppercase hover:border-red-500/40 hover:bg-red-500/15 hover:text-red-200 transition-all backdrop-blur-md"
        >
          <Power className="mr-1.5 size-3.5" />
          Encerrar Sistemas
        </Button>
      </section>

      {/* Rodapé Futurista */}
      <div className="fixed bottom-5 left-0 flex w-full items-center justify-center select-none pointer-events-none">
        <p className="font-mono text-[10px] tracking-[0.25em] text-orange-400/50 uppercase">
          QUANTUM ENCRYPTED TUNNEL ACTIVE · JARVIS SYSTEM {version.toUpperCase()}
        </p>
      </div>
    </div>
  );
};
