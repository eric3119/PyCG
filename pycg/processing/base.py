import ast
import os

from pycg import utils
from pycg.machinery.definitions import Definition

class ProcessingBase(ast.NodeVisitor):
    def __init__(self, filename, modname, modules_analyzed=None):
        self.modname = modname

        self.modules_analyzed = set()
        if modules_analyzed:
            self.modules_analyzed = modules_analyzed
        self.modules_analyzed.add(self.modname)

        self.filename = os.path.abspath(filename)

        with open(filename, "rt") as f:
            self.contents = f.read()

        self.name_stack = []
        self.last_called_names = None

    def get_modules_analyzed(self):
        return self.modules_analyzed

    def merge_modules_analyzed(self, analyzed):
        self.modules_analyzed = self.modules_analyzed.union(analyzed)

    @property
    def current_ns(self):
        return ".".join(self.name_stack)

    def visit_Module(self, node):
        self.name_stack.append(self.modname)
        self.scope_manager.get_scope(self.modname).reset_counters()
        self.generic_visit(node)
        self.name_stack.pop()

    def visit_FunctionDef(self, node):
        self.name_stack.append(node.name)
        self.scope_manager.get_scope(self.current_ns).reset_counters()
        for stmt in node.body:
            self.visit(stmt)
        self.name_stack.pop()

    def visit_Lambda(self, node, lambda_name=None):
        self.name_stack.append(lambda_name)
        self.visit(node.body)
        self.name_stack.pop()

    def visit_BinOp(self, node):
        self.visit(node.left)
        self.visit(node.right)

    def visit_ClassDef(self, node):
        self.name_stack.append(node.name)
        self.scope_manager.get_scope(self.current_ns).reset_counters()
        for stmt in node.body:
            self.visit(stmt)
        self.name_stack.pop()

    def _handle_assign(self, targetns, decoded):
        defi = self.def_manager.get(targetns)
        if not defi:
            defi = self.def_manager.create(targetns, utils.constants.NAME_DEF)

        for d in decoded:
            if isinstance(d, Definition):
                defi.get_name_pointer().add(d.get_ns())
            else:
                defi.get_lit_pointer().add(d)
        return defi

    def _visit_return(self, node):
        self.visit(node.value)

        return_ns = utils.join_ns(self.current_ns, utils.constants.RETURN_NAME)
        self._handle_assign(return_ns, self.decode_node(node.value))

    def _visit_assign(self, node):
        self.visit(node.value)

        decoded = self.decode_node(node.value)

        def do_assign(decoded, target):
            self.visit(target)
            if isinstance(target, ast.Tuple):
                for pos, elt in enumerate(target.elts):
                    do_assign(decoded[pos], elt)
            else:
                targetns = utils.join_ns(self.current_ns, target.id)
                defi = self._handle_assign(targetns, decoded)
                self.scope_manager.handle_assign(self.current_ns, target.id, defi)

        for target in node.targets:
            do_assign(decoded, target)

    def decode_node(self, node):
        if isinstance(node, ast.Name):
            return [self.scope_manager.get_def(self.current_ns, node.id)]
        elif isinstance(node, ast.Call):
            decoded = self.decode_node(node.func)
            return_defs = []
            for called_def in decoded:
                if not isinstance(called_def, Definition):
                    continue

                return_ns = utils.constants.INVALID_NAME
                if called_def.get_type() == utils.constants.FUN_DEF:
                    return_ns = utils.join_ns(called_def.get_ns(), utils.constants.RETURN_NAME)
                elif called_def.get_type() == utils.constants.CLS_DEF:
                    return_ns = called_def.get_ns()
                defi = self.def_manager.get(return_ns)
                if defi:
                    return_defs.append(defi)

            return return_defs
        elif isinstance(node, ast.Lambda):
            lambda_counter = self.scope_manager.get_scope(self.current_ns).get_lambda_counter()
            lambda_name = utils.get_lambda_name(lambda_counter)
            return [self.scope_manager.get_def(self.current_ns, lambda_name)]
        elif isinstance(node, ast.Tuple):
            decoded = []
            for elt in node.elts:
                decoded.append(self.decode_node(elt))
            return decoded
        elif isinstance(node, ast.BinOp):
            decoded_left = self.decode_node(node.left)
            decoded_right = self.decode_node(node.right)
            # return the non definition types if we're talking about a binop
            # since we only care about the type of the return (num, str, etc)
            if not isinstance(decoded_left, Definition):
                return decoded_left
            if not isinstance(decoded_right, Definition):
                return decoded_right
        elif isinstance(node, ast.Attribute):
            names = self._retrieve_attribute_names(node)
            defis = []
            for name in names:
                defi = self.def_manager.get(name)
                if defi:
                    defis.append(defi)
            return defis
        elif isinstance(node, ast.Num):
            return [node.n]
        elif isinstance(node, ast.Str):
            return [node.s]
        else:
            return []

    def _retrieve_attribute_names(self, node):
        if not isinstance(node, ast.Attribute):
            raise Exception("The node is not an attribute")

        if not getattr(self, "closured", None):
            return []

        decoded = self.decode_node(node.value)
        if not decoded:
            return []

        names = set()
        for parent in decoded:
            closured = self.closured.get(parent.get_ns())
            for name in closured:
                defi = self.def_manager.get(name)
                if not defi:
                    continue
                if defi.get_type() == utils.constants.CLS_DEF:
                    names.add(self.find_cls_fun_ns(defi.get_ns(), node.attr))
                if defi.get_type() == utils.constants.FUN_DEF:
                    names.add(utils.join_ns(name, node.attr))
        return names

    def iterate_call_args(self, defi, node):
        for pos, arg in enumerate(node.args):
            self.visit(arg)
            decoded = self.decode_node(arg)
            if defi.is_function_def():
                pos_arg_names = defi.get_name_pointer().get_pos_arg(pos)
                # if arguments for this position exist update their namespace
                for name in pos_arg_names:
                    arg_def = self.def_manager.get(name)
                    for d in decoded:
                        if isinstance(d, Definition):
                            arg_def.get_name_pointer().add(d.get_ns())
                        else:
                            arg_def.get_lit_pointer().add(d)
            else:
                for d in decoded:
                    if isinstance(d, Definition):
                        defi.get_name_pointer().add_pos_arg(pos, None, d.get_ns())
                    else:
                        defi.get_name_pointer().add_pos_lit_arg(pos, None, d)

        for keyword in node.keywords:
            self.visit(keyword.value)
            decoded = self.decode_node(keyword.value)
            if defi.is_function_def():
                arg_names = defi.get_name_pointer().get_arg(keyword.arg)
                for name in arg_names:
                    arg_def = self.def_manager.get(name)
                    for d in decoded:
                        if isinstance(d, Definition):
                            arg_def.get_name_pointer().add(d.get_ns())
                        else:
                            arg_def.get_lit_pointer().add(d)
            else:
                for d in decoded:
                    if isinstance(d, Definition):
                        defi.get_name_pointer().add_arg(keyword.arg, d.get_ns())
                    else:
                        defi.get_name_pointer().add_lit_arg(keyword.arg, d)

    def retrieve_call_names(self, node):
        names = set()
        if isinstance(node.func, ast.Name):
            defi = self.scope_manager.get_def(self.current_ns, node.func.id)
            if defi:
                names = self.closured.get(defi.get_ns(), None)
        elif isinstance(node.func, ast.Call):
            for name in self.last_called_names:
                return_ns = utils.join_ns(name, utils.constants.RETURN_NAME)
                returns = self.closured.get(return_ns)
                if not returns:
                    continue
                for ret in returns:
                    defi = self.def_manager.get(ret)
                    names.add(defi.get_ns())
        elif isinstance(node.func, ast.Attribute):
            names = self._retrieve_attribute_names(node.func)

        return names

    def analyze_submodules(self, cls, *args, **kwargs):
        imports = self.import_manager.get_imports(self.modname)

        for imp in imports:
            self.analyze_submodule(cls, imp, *args, **kwargs)

    def analyze_submodule(self, cls, imp, *args, **kwargs):
        if imp in self.get_modules_analyzed():
            return

        fname = self.import_manager.get_filepath(imp)

        self.import_manager.set_current_mod(imp)

        visitor = cls(fname, imp, *args, **kwargs)
        visitor.analyze()
        self.merge_modules_analyzed(visitor.get_modules_analyzed())

        self.import_manager.set_current_mod(self.modname)

    def find_cls_fun_ns(self, cls_name, fn):
        cls = self.class_manager.get(cls_name)
        if not cls:
            return

        for item in cls.get_mro():
            ns = utils.join_ns(item, fn)
            if self.def_manager.get(ns):
                return ns
