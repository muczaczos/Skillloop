import { useId } from "react";

type TextFieldProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "id"> & {
  label: string;
  hint?: string;
  error?: string;
};

export function TextField({ label, hint, error, ...props }: TextFieldProps) {
  // Generated ids: a previous page can stay mounted (hidden) next to the current one.
  const id = useId();
  const messageId = `${id}-message`;
  const message = error ?? hint;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={message ? messageId : undefined}
        // 16px text: smaller text makes mobile Safari zoom in on focus.
        className="min-h-11 rounded-lg border border-slate-300 bg-white px-3 text-base focus:border-indigo-500 focus:outline-2 focus:outline-indigo-500 aria-invalid:border-red-500 dark:border-slate-700 dark:bg-slate-900"
        {...props}
      />
      {message && (
        <p
          id={messageId}
          className={`text-sm ${error ? "text-red-700 dark:text-red-400" : "text-slate-500 dark:text-slate-400"}`}
        >
          {message}
        </p>
      )}
    </div>
  );
}
