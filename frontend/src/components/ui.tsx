import type { ReactNode } from 'react';

export function GridBackground() {
  return (
    <div
      className="fixed inset-0 pointer-events-none opacity-[0.04] z-0"
      style={{
        backgroundImage: 'radial-gradient(circle at 1px 1px, black 1px, transparent 0)',
        backgroundSize: '32px 32px'
      }}
    />
  );
}

export function Badge({ children, variant = 'default' }: { children: ReactNode; variant?: 'default' | 'inverted' | 'warning' | 'danger' }) {
  const styles = {
    default: 'border-black text-black bg-[#F4F4F0]',
    inverted: 'border-black bg-black text-[#F4F4F0]',
    warning: 'border-black bg-[#F4F4F0] text-black decoration-wavy underline decoration-1',
    danger: 'border-black bg-white text-black underline decoration-2'
  };
  return <span className={`px-2 py-0.5 text-[10px] font-mono tracking-widest uppercase border ${styles[variant]}`}>{children}</span>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="border border-black border-dashed p-10 text-center text-[10px] font-mono tracking-widest text-gray-500 uppercase">
      {children}
    </div>
  );
}
