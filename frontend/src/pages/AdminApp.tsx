import React, { useState } from 'react';
import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { DocumentsPanel } from '../components/DocumentsPanel';
import { AdminDashboard } from '../components/AdminDashboard';
import { ChatWindow } from '../components/ChatWindow';
import { Bot, FolderOpen, BarChart3, MessageSquare } from 'lucide-react';

export function AdminApp() {
  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header — same visual style as UserApp */}
      <header className="bg-white border-b border-gray-200 h-12 flex items-center px-5 shadow-sm shrink-0">
        <div className="flex items-center gap-2.5 mr-6">
          <div className="w-7 h-7 bg-gradient-to-br from-blue-500 to-violet-600 rounded-lg flex items-center justify-center">
            <Bot size={15} className="text-white" />
          </div>
          <span className="font-semibold text-gray-800 text-sm tracking-tight">Trợ lý Hành chính số</span>
          <span className="text-[10px] bg-orange-100 text-orange-700 border border-orange-200 rounded-full px-2 py-0.5 font-medium">Admin</span>
        </div>

        <nav className="flex items-center gap-1">
          <NavLink to="/admin/chat"
            className={({ isActive }) =>
              `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                isActive ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
              }`
            }>
            <MessageSquare size={14} /> Chat
          </NavLink>
          <NavLink to="/admin/documents"
            className={({ isActive }) =>
              `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                isActive ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
              }`
            }>
            <FolderOpen size={14} /> Tài liệu
          </NavLink>
          <NavLink to="/admin/monitor"
            className={({ isActive }) =>
              `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                isActive ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
              }`
            }>
            <BarChart3 size={14} /> Giám sát
          </NavLink>
        </nav>
      </header>

      <div className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="/admin/chat" replace />} />
          <Route path="/chat" element={<ChatWindow />} />
          <Route path="/documents" element={<DocumentsPanel />} />
          <Route path="/monitor" element={<AdminDashboard />} />
        </Routes>
      </div>
    </div>
  );
}
