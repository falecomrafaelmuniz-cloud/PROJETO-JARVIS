import { useState } from 'react';
import { Check, Power, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

function WelcomeImage() {
  return (
    <svg
      width="64"
      height="64"
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="text-fg0 mb-4 size-16"
    >
      <path
        d="M15 24V40C15 40.7957 14.6839 41.5587 14.1213 42.1213C13.5587 42.6839 12.7956 43 12 43C11.2044 43 10.4413 42.6839 9.87868 42.1213C9.31607 41.5587 9 40.7957 9 40V24C9 23.2044 9.31607 22.4413 9.87868 21.8787C10.4413 21.3161 11.2044 21 12 21C12.7956 21 13.5587 21.3161 14.1213 21.8787C14.6839 22.4413 15 23.2044 15 24ZM22 5C21.2044 5 20.4413 5.31607 19.8787 5.87868C19.3161 6.44129 19 7.20435 19 8V56C19 56.7957 19.3161 57.5587 19.8787 58.1213C20.4413 58.6839 21.2044 59 22 59C22.7956 59 23.5587 58.6839 24.1213 58.1213C24.6839 57.5587 25 56.7957 25 56V8C25 7.20435 24.6839 6.44129 24.1213 5.87868C23.5587 5.31607 22.7956 5 22 5ZM32 13C31.2044 13 30.4413 13.3161 29.8787 13.8787C29.3161 14.4413 29 15.2044 29 16V48C29 48.7957 29.3161 49.5587 29.8787 50.1213C30.4413 50.6839 31.2044 51 32 51C32.7956 51 33.5587 50.6839 34.1213 50.1213C34.6839 49.5587 35 48.7957 35 48V16C35 15.2044 34.6839 14.4413 34.1213 13.8787C33.5587 13.3161 32.7956 13 32 13ZM42 21C41.2043 21 40.4413 21.3161 39.8787 21.8787C39.3161 22.4413 39 23.2044 39 24V40C39 40.7957 39.3161 41.5587 39.8787 42.1213C40.4413 42.6839 41.2043 43 42 43C42.7957 43 43.5587 42.6839 44.1213 42.1213C44.6839 41.5587 45 40.7957 45 40V24C45 23.2044 44.6839 22.4413 44.1213 21.8787C43.5587 21.3161 42.7957 21 42 21ZM52 17C51.2043 17 50.4413 17.3161 49.8787 17.8787C49.3161 18.4413 49 19.2044 49 20V44C49 44.7957 49.3161 45.5587 49.8787 46.1213C50.4413 46.6839 51.2043 47 52 47C52.7957 47 53.5587 46.6839 54.1213 46.1213C54.6839 45.5587 55 44.7957 55 44V20C55 19.2044 54.6839 18.4413 54.1213 17.8787C53.5587 17.3161 52.7957 17 52 17Z"
        fill="currentColor"
      />
    </svg>
  );
}

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
  const [updateState, setUpdateState] = useState<'idle' | 'updating' | 'success' | 'error'>('idle');
  const [updateMessage, setUpdateMessage] = useState<string | null>(null);

  const handleUpdateJarvis = async () => {
    if (updateState === 'updating') return;
    setUpdateState('updating');
    setUpdateMessage('Buscando melhorias no GitHub e atualizando sistemas...');

    try {
      const response = await fetch('/api/update', { method: 'POST' });
      const data = await response.json();

      if (!response.ok || data.status === 'error') {
        throw new Error(data.message || 'Falha ao atualizar o Jarvis.');
      }

      setUpdateState('success');
      setUpdateMessage(data.message || 'Jarvis atualizado com sucesso!');

      setTimeout(() => {
        window.location.reload();
      }, 1500);
    } catch (error) {
      setUpdateState('error');
      setUpdateMessage(
        error instanceof Error ? error.message : 'Erro ao buscar atualizações do Jarvis.'
      );
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
      <section className="bg-background flex flex-col items-center justify-center text-center">
        <div className="mb-6 flex flex-col items-center">
          <div className="relative mb-3 flex size-20 items-center justify-center rounded-3xl border border-[#ff5500]/40 bg-[#ff5500]/10 shadow-[0_0_35px_rgba(255,85,0,0.35)] backdrop-blur-xl">
            <span className="size-4 rounded-full bg-[#ff5500] animate-pulse shadow-[0_0_15px_#ff5500]" />
          </div>
          <h1 className="font-mono text-2xl font-bold tracking-[0.3em] uppercase text-white drop-shadow-[0_0_12px_rgba(255,85,0,0.5)]">
            JARVIS <span className="text-[#ff5500]">OSONE</span>
          </h1>
          <p className="mt-1 font-serif italic text-xs tracking-wider text-orange-200/70">
            Sistema Neural de Inteligência e Automação Desktop
          </p>
        </div>

        <Button
          size="lg"
          onClick={onStartCall}
          disabled={updateState === 'updating'}
          className="mt-2 w-72 h-12 rounded-2xl bg-gradient-to-r from-[#ff6b00] to-[#ff4500] font-mono text-xs font-bold tracking-widest text-white uppercase shadow-[0_0_30px_rgba(255,85,0,0.45)] hover:from-[#ff7a1a] hover:to-[#ff5500] hover:shadow-[0_0_40px_rgba(255,85,0,0.65)] transition-all active:scale-[0.98] border border-orange-400/30"
        >
          {startButtonText === 'Iniciar Chamada' ? 'INICIAR NEURAL LINK' : startButtonText}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={handleUpdateJarvis}
          disabled={updateState === 'updating'}
          className="mt-3.5 w-72 rounded-2xl border-orange-500/30 bg-orange-500/10 font-mono text-[11px] font-semibold tracking-wider text-orange-300 uppercase hover:border-orange-500/50 hover:bg-orange-500/20 hover:text-orange-200 backdrop-blur-md"
        >
          {updateState === 'updating' ? (
            <>
              <RefreshCw className="mr-1.5 size-3.5 animate-spin text-[#ff5500]" />
              Baixando Atualização...
            </>
          ) : updateState === 'success' ? (
            <>
              <Check className="mr-1.5 size-3.5 text-emerald-400" />
              Sistema Atualizado!
            </>
          ) : (
            <>
              <RefreshCw className="mr-1.5 size-3.5" />
              Checar Atualizações
            </>
          )}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={handleShutdownSystem}
          disabled={updateState === 'updating'}
          className="mt-3 w-72 rounded-2xl border-red-500/30 bg-red-500/10 font-mono text-[11px] font-semibold tracking-wider text-red-300 uppercase hover:border-red-500/50 hover:bg-red-500/20 hover:text-red-200 backdrop-blur-md"
        >
          <Power className="mr-1.5 size-3.5" />
          Encerrar Sistemas
        </Button>

        {updateMessage && (
          <p
            className={`mt-3 max-w-xs text-xs font-medium ${
              updateState === 'error'
                ? 'text-red-400'
                : updateState === 'success'
                  ? 'text-emerald-400'
                  : 'text-orange-300'
            }`}
          >
            {updateMessage}
          </p>
        )}
      </section>

      <div className="fixed bottom-5 left-0 flex w-full items-center justify-center select-none pointer-events-none">
        <p className="font-mono text-[10px] tracking-[0.25em] text-orange-400/50 uppercase">
          QUANTUM ENCRYPTED TUNNEL ACTIVE · OSONE SYSTEM V3.0
        </p>
      </div>
    </div>
  );
};
