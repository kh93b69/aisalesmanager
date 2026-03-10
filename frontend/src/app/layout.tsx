import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Sales Manager",
  description: "Управление ИИ-менеджерами для мессенджеров",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif" }}>
        {children}
      </body>
    </html>
  );
}
