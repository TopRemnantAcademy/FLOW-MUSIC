import CreateForm from "@/components/CreateForm";
import { resolveLocale } from "@/lib/i18n-server";
import { translate } from "@/lib/i18n-catalog";

export default async function CreatePage() {
  const locale = await resolveLocale();
  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-bold">{translate(locale, "create.title")}</h1>
      <CreateForm />
    </main>
  );
}
