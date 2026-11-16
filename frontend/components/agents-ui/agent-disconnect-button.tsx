'use client';

import { type VariantProps } from 'class-variance-authority';
import { PhoneOffIcon } from 'lucide-react';
import { useSessionContext } from '@livekit/components-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/shadcn/utils';

export interface AgentDisconnectButtonProps
  extends React.ComponentProps<'button'>, VariantProps<typeof buttonVariants> {
  icon?: React.ReactNode;
  children?: React.ReactNode;
}

export function AgentDisconnectButton({
  icon,
  size = 'default',
  variant = 'destructive',
  children,
  onClick,
  ...props
}: AgentDisconnectButtonProps) {
  const { end } = useSessionContext();
  const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    onClick?.(event);
    end();
  };

  return (
    <Button variant={variant} size={size} onClick={handleClick} {...props}>
      {icon ?? <PhoneOffIcon />}
      {children !== undefined ? (
        children
      ) : (
        <span className={cn(size?.includes('icon') && 'sr-only')}>ENCERRAR CHAMADA</span>
      )}
    </Button>
  );
}
