import { useMemo } from 'react';
import { Cpu, Zap } from 'lucide-react';

export interface SkillOutput {
  skill: string;
  task_id: string;
  domain: string;
  output: string;
  tokens?: number;
  done: boolean;
}

interface Props {
  outputs: Map<string, SkillOutput>;
}

export default function SkillOutputViewer({ outputs }: Props) {
  const list = useMemo(() => {
    const arr = Array.from(outputs.values());
    // show latest 5, most recent first
    return arr.slice(-5).reverse();
  }, [outputs]);

  if (list.length === 0) return null;

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <div className="flex items-center gap-2 mb-3">
        <Cpu className="w-4 h-4 text-blue-500" />
        <h2 className="text-gray-900 font-semibold text-sm">技能输出</h2>
      </div>
      <div className="space-y-3">
        {list.map((item) => {
          const key = `${item.task_id}__${item.skill}`;
          return (
            <div
              key={key}
              className={`rounded-lg border p-3 transition-colors ${
                item.done ? 'border-green-200 bg-green-50/30' : 'border-blue-200 bg-blue-50/30 animate-pulse'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-bold text-gray-900">{item.skill}</span>
                  <span className="text-[10px] text-gray-400">@ {item.task_id}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{item.domain}</span>
                </div>
                <div className="flex items-center gap-2">
                  {!item.done && (
                    <span className="flex items-center gap-1 text-[10px] text-blue-600">
                      <Zap className="w-2.5 h-2.5 animate-pulse" /> 生成中
                    </span>
                  )}
                  {item.tokens !== undefined && (
                    <span className="text-[10px] text-gray-400">{item.tokens} tok</span>
                  )}
                </div>
              </div>
              <pre className="whitespace-pre-wrap text-xs text-gray-700 font-mono max-h-40 overflow-auto leading-relaxed">
                {item.output || (item.done ? '(空输出)' : '等待输出...')}
              </pre>
            </div>
          );
        })}
      </div>
    </div>
  );
}
