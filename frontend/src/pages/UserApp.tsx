import React from 'react';
import { ChatWindow } from '../components/ChatWindow';
import { Bot } from 'lucide-react';

export function UserApp() {
  return (
    <div className="flex flex-col h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 h-12 flex items-center px-5 shadow-sm shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-gradient-to-br from-blue-500 to-violet-600 rounded-lg flex items-center justify-center">
            <Bot size={15} className="text-white" />
          </div>
          <span className="font-semibold text-gray-800 text-sm tracking-tight">Trợ lý Hành chính số</span>
        </div>
      </header>
      <div className="flex-1 overflow-hidden">
        <ChatWindow />
      </div>
    </div>
  );
}
