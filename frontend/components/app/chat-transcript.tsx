'use client';

import { AnimatePresence, type HTMLMotionProps, motion } from 'motion/react';
import { type ReceivedMessage, useAgent } from '@livekit/components-react';
import { AgentChatTranscript } from '@/components/agents-ui/agent-chat-transcript';
import { cn } from '@/lib/shadcn/utils';

const MotionContainer = motion.create('div');

const CONTAINER_MOTION_PROPS: HTMLMotionProps<'div'> = {
  variants: {
    hidden: {
      opacity: 0,
      transition: {
        ease: 'easeOut',
        duration: 0.25,
      },
    },
    visible: {
      opacity: 1,
      transition: {
        delay: 0.1,
        ease: 'easeOut',
        duration: 0.25,
      },
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
};

interface ChatTranscriptProps {
  hidden?: boolean;
  messages?: ReceivedMessage[];
}

export function ChatTranscript({
  hidden = false,
  messages = [],
  className,
  ...props
}: ChatTranscriptProps & Omit<HTMLMotionProps<'div'>, 'ref'>) {
  const { state: agentState } = useAgent();

  return (
    <div
      className={cn(
        'absolute inset-x-0 top-16 bottom-[175px] z-20 flex w-full flex-col md:bottom-[210px]',
        hidden ? 'pointer-events-none' : 'pointer-events-auto'
      )}
    >
      <AnimatePresence>
        {!hidden && (
          <MotionContainer
            {...props}
            {...CONTAINER_MOTION_PROPS}
            className={cn('flex h-full w-full flex-col gap-3', className)}
          >
            <AgentChatTranscript
              agentState={agentState}
              messages={messages}
              className={cn(
                'mx-auto w-full max-w-2xl',
                '[&>div]:overflow-y-auto [&>div]:[scrollbar-width:thin]',
                '[&>div>div]:gap-3 [&>div>div]:px-4 [&>div>div]:pt-6 [&>div>div]:pb-4 md:[&>div>div]:px-6',
                '[&_.is-user>div]:rounded-[20px] [&_.is-user>div]:border [&_.is-user>div]:border-cyan-400/40 [&_.is-user>div]:bg-[#1da3b9]/25 [&_.is-user>div]:text-white [&_.is-user>div]:backdrop-blur-md',
                '[&_.is-assistant>div]:rounded-[20px] [&_.is-assistant>div]:border [&_.is-assistant>div]:border-cyan-500/20 [&_.is-assistant>div]:bg-black/70 [&_.is-assistant>div]:px-4 [&_.is-assistant>div]:py-3 [&_.is-assistant>div]:text-white/95 [&_.is-assistant>div]:backdrop-blur-md'
              )}
            />
          </MotionContainer>
        )}
      </AnimatePresence>
    </div>
  );
}
