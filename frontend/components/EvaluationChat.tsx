import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { FiSend, FiTrash2 } from 'react-icons/fi';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface EvaluationChatProps {
  roleId: string;
  messages: Message[];
  onMessagesChange: (messages: Message[] | ((prev: Message[]) => Message[])) => void;
  onClearChat?: () => void;
  /** Called when a new assistant reply is received so the parent can persist immediately (survives tab switch). */
  onPersistMessages?: (messages: Message[]) => void;
}

/** FastAPI may return detail as a string or a list of validation objects { msg?: string }. */
function formatAxiosDetail(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail;
  if (!Array.isArray(detail)) return undefined;
  const parts = detail.map((item) => {
    if (item && typeof item === 'object' && 'msg' in item) {
      const m = (item as { msg?: unknown }).msg;
      if (m == null) return '';
      return typeof m === 'string' ? m : String(m);
    }
    return '';
  }).filter(Boolean);
  return parts.length ? parts.join('; ') : undefined;
}

export default function EvaluationChat({ roleId, messages, onMessagesChange, onClearChat, onPersistMessages }: EvaluationChatProps) {
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const setMessages = onMessagesChange;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: Message = { role: 'user', content: input.trim() };
    const messagesWithUser = [...messages, userMessage];
    setMessages(() => messagesWithUser);
    setInput('');
    setIsLoading(true);

    try {
      const response = await axios.post(
        `/api/roles/${roleId}/candidates/evaluate`,
        {
          question: input.trim(),
          conversation_history: messages.map((m) => ({ role: m.role, content: m.content })),
        },
        {
          headers: { 'Content-Type': 'application/json' },
          timeout: 120000, // 2 minutes - evaluation can take a while
        }
      );

      const content = response.data?.response ?? response.data?.detail ?? 'No response received';
      const assistantMessage: Message = {
        role: 'assistant',
        content: typeof content === 'string' ? content : JSON.stringify(content),
      };
      const newMessages = [...messagesWithUser, assistantMessage];
      setMessages(() => newMessages);
      // Persist immediately so reply is saved even if user switches tab before state propagates
      onPersistMessages?.(newMessages);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: unknown; response?: string } }; message?: string };
      console.error('Error evaluating:', err);
      const detail = err.response?.data?.detail;
      const backendMessage = err.response?.data?.response ?? formatAxiosDetail(detail);
      const errorContent = backendMessage
        ? String(backendMessage)
        : err.message?.includes('timeout') || err.message?.includes('Timeout')
          ? 'The request took too long. The evaluation may still be processing. Please try again.'
          : 'Sorry, I encountered an error. Please try again.';
      const newMessages = [...messagesWithUser, { role: 'assistant' as const, content: errorContent }];
      setMessages(() => newMessages);
      onPersistMessages?.(newMessages);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-lg shadow h-[600px] flex flex-col">
      <div className="p-4 border-b flex justify-between items-start gap-4">
        <div>
          <h2 className="text-xl font-semibold">Candidate Evaluation Chat</h2>
          <p className="text-sm text-gray-600">Ask questions about candidates in the Evaluation column who have completed interviews</p>
        </div>
        {onClearChat && messages.length > 0 && (
          <button
            onClick={onClearChat}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition shrink-0"
            title="Clear chat"
          >
            <FiTrash2 className="w-4 h-4" />
            Clear chat
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 py-8">
            <p>Start a conversation to evaluate candidates</p>
            <p className="text-sm mt-2">
              Try asking:{' '}
              <span className="whitespace-nowrap">&ldquo;Who is the best fit for this role?&rdquo;</span>
            </p>
          </div>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[80%] rounded-lg p-3 ${
                message.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-900'
              }`}
            >
              <p className="whitespace-pre-wrap">{message.content}</p>
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 rounded-lg p-3">
              <p className="text-gray-600">Thinking...</p>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 border-t">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSend()}
            placeholder="Ask about candidates..."
            className="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            disabled={isLoading}
          />
          <button
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <FiSend className="w-4 h-4" />
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

