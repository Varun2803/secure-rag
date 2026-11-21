import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import type { FieldValues, Path, UseFormRegister } from 'react-hook-form'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type PasswordFieldProps<TFieldValues extends FieldValues> = {
  id: string
  label: string
  autoComplete: 'current-password' | 'new-password'
  describedBy?: string
  invalid?: boolean
  register: UseFormRegister<TFieldValues>
  name: Path<TFieldValues>
  onChange?: () => void
}

export function PasswordField<TFieldValues extends FieldValues>({ id, label, autoComplete, describedBy, invalid, register, name, onChange }: PasswordFieldProps<TFieldValues>) {
  const [visible, setVisible] = useState(false)
  const field = register(name)

  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium" htmlFor={id}>{label}</label>
      <div className="relative">
        <Input
          {...field}
          id={id}
          autoComplete={autoComplete}
          type={visible ? 'text' : 'password'}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          onChange={(event) => {
            void field.onChange(event)
            onChange?.()
          }}
          className="pr-12"
        />
        <Button
          aria-label={`${visible ? 'Hide' : 'Show'} ${label.toLowerCase()}`}
          className="absolute right-1 top-1 h-8 w-9"
          onClick={() => setVisible((current) => !current)}
          size="icon"
          type="button"
          variant="ghost"
        >
          {visible ? <EyeOff aria-hidden="true" className="size-4" /> : <Eye aria-hidden="true" className="size-4" />}
        </Button>
      </div>
    </div>
  )
}
