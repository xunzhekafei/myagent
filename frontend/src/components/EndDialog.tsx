/** 「立即结束」确认框：保留记录出报告，或直接删掉这一场。 */
export default function EndDialog({
  answered,
  onKeep,
  onDelete,
  onCancel,
}: {
  answered: boolean;
  onKeep: () => void;
  onDelete: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="end-dialog-title"
    >
      <div className="modal">
        <h3 id="end-dialog-title">立即结束这场面试？</h3>
        <p>
          {answered
            ? "保留记录会立即生成评分报告；删除会移除这一场的全部问答，且无法恢复。"
            : "这场还没有回答，无法生成评分报告。你可以直接删除这条记录。"}
        </p>
        <div className="modal-actions">
          <button className="secondary" onClick={onCancel}>
            取消
          </button>
          <button className="secondary danger" onClick={onDelete}>
            删除记录（不可恢复）
          </button>
          <button className="primary" onClick={onKeep} disabled={!answered}>
            保留并生成报告
          </button>
        </div>
      </div>
    </div>
  );
}
