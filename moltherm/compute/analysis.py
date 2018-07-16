from os import listdir
from os.path import join, isfile, isdir, abspath
import shutil

from atomate.qchem.database import QChemCalcDb

from moltherm.compute.outputs import QCOutput
from moltherm.compute.utils import get_molecule, extract_id

__author__ = "Evan Spotte-Smith"
__version__ = "0.1"
__maintainer__ = "Evan Spotte-Smith"
__email__ = "espottesmith@gmail.com"
__status__ = "Alpha"
__date__ = "July 2018"


class MolThermAnalysis:
    """
    This class can be used to analyze data from MolThermWorkflow workflows,
    including extracting thermo data from calculations and generating predicted
    boiling and melting points.
    """

    def __init__(self, base_dir, reactant_pre="rct_", product_pre="pro_",
                 db_file="db.json"):
        """
        :param base_dir: Directory where input and output data should be stored.
        :param reactant_pre: Prefix for reactant files.
        :param product_pre: Prefix for product files.
        :param db_file: Path to database config file.
        """

        self.base_dir = base_dir
        self.reactant_pre = reactant_pre
        self.product_pre = product_pre
        self.db_file = db_file

        try:
            self.db = QChemCalcDb.from_db_file(self.db_file)
        except:
            self.db = None

    def quick_check(self, dirs):
        """
        Returns only those reactions which have appropriate products and
        reactants (products, reactants have same number of atoms).

        This is not a sophisticated checking mechanism, and could probably be
        easily improved upon.

        :return:
        """

        add_up = []

        for d in dirs:
            path = join(self.base_dir, d)
            files = [f for f in listdir(path) if isfile(join(path, f))]
            rcts = [f for f in files if f.startswith(self.reactant_pre) and f.endswith(".mol")]
            pros = [f for f in files if f.startswith(self.product_pre) and f.endswith(".mol")]

            rct_mols = [get_molecule(join(self.base_dir, d, r)) for r in rcts]
            pro_mols = [get_molecule(join(self.base_dir, d, p)) for p in pros]

            total_pro_length = sum([len(p) for p in pro_mols])
            total_rct_length = sum([len(r) for r in rct_mols])

            if total_pro_length == total_rct_length:
                add_up.append(d)

        return add_up

    def get_reaction_thermo_files(self, path=None):
        """
        Naively scrape thermo data from QChem output files.

        :param path: Path to a subdirectory.

        :return: dict {prop: value}, where properties are enthalpy, entropy.
        """

        if path is not None:
            base_path = join(self.base_dir, path)
        else:
            base_path = self.base_dir

        rct_ids = [extract_id(f) for f in listdir(base_path) if
                   f.endswith(".mol") and f.startswith(self.reactant_pre)]

        pro_ids = [extract_id(f) for f in listdir(base_path) if
                   f.endswith(".mol") and f.startswith(self.product_pre)]

        rct_map = {m: [f for f in listdir(base_path) if
                       f.startswith(self.reactant_pre) and m in f
                       and ".out" in f and not f.endswith("_copy")]
                   for m in rct_ids}
        pro_map = {m: [f for f in listdir(base_path)
                       if f.startswith(self.product_pre) and m in f
                       and ".out" in f] for m in pro_ids}

        rct_thermo = {"enthalpy": 0, "entropy": 0, "energy": 0, "has_sp": {}}
        pro_thermo = {"enthalpy": 0, "entropy": 0, "energy": 0, "has_sp": {}}

        for mol in rct_map.keys():
            enthalpy = 0
            entropy = 0
            energy_opt = 0
            energy_sp = 0

            for out in rct_map[mol]:
                qcout = QCOutput(join(base_path, out))

                # Catch potential for Nonetype entries
                if "freq" in out:
                    enthalpy = qcout.data.get("enthalpy", 0) or 0
                    entropy = qcout.data.get("entropy", 0) or 0
                elif "opt" in out:
                    energy_opt = qcout.data.get("final_energy", 0) or 0
                elif "sp" in out:
                    energy_sp = qcout.data.get("final_energy_sp", 0) or 0

            if energy_sp == 0:
                rct_thermo["energy"] += energy_opt
                rct_thermo["has_sp"][self.reactant_pre + str(mol)] = False
            else:
                rct_thermo["energy"] += energy_sp
                rct_thermo["has_sp"][self.reactant_pre + str(mol)] = True

            rct_thermo["enthalpy"] += enthalpy
            rct_thermo["entropy"] += entropy
            print(path, mol, enthalpy, energy_sp)

        for mol in pro_map.keys():
            enthalpy = 0
            entropy = 0
            energy_opt = 0
            energy_sp = 0

            for out in pro_map[mol]:
                qcout = QCOutput(join(base_path, out))

                # Catch potential for Nonetype entries
                if "freq" in out:
                    enthalpy = qcout.data.get("enthalpy", 0) or 0
                    entropy = qcout.data.get("entropy", 0) or 0
                elif "opt" in out:
                    energy_opt = qcout.data.get("final_energy", 0) or 0
                elif "sp" in out:
                    energy_sp = qcout.data.get("final_energy_sp", 0) or 0

            # Enthalpy calculation should actually be enthalpy - energy_sp
            # But currently, not all calculations have sp
            if int(energy_sp) == 0:
                pro_thermo["energy"] += energy_opt
                pro_thermo["has_sp"][self.product_pre + str(mol)] = False
            else:
                pro_thermo["energy"] += energy_sp
                pro_thermo["has_sp"][self.product_pre + str(mol)] = True

            pro_thermo["enthalpy"] += enthalpy
            pro_thermo["entropy"] += entropy
            print(path, mol, enthalpy, energy_sp)

        thermo_data = {}

        # Generate totals as ∆H = H_pro - H_rct, ∆S = S_pro - S_rct
        # Also ensures that units are appropriate (Joules/mol,
        # rather than cal/mol or kcal/mol, or hartree for energy)
        energy = (pro_thermo["energy"] - rct_thermo["energy"]) * 627.509
        enthalpy = (pro_thermo["enthalpy"] - rct_thermo["enthalpy"])
        print(path, energy, enthalpy)
        thermo_data["enthalpy"] = (energy + enthalpy) * 1000 * 4.184
        thermo_data["entropy"] = (pro_thermo["entropy"] - rct_thermo["entropy"]) * 4.184
        try:
            thermo_data["t_critical"] = thermo_data["enthalpy"] / thermo_data["entropy"]
        except ZeroDivisionError:
            thermo_data["t_critical"] = None
        # Combine dicts from pro_thermo and rct_thermo
        thermo_data["has_sp"] = {**pro_thermo["has_sp"], **rct_thermo["has_sp"]}

        result = {"thermo": thermo_data,
                  "directory": path,
                  "reactant_ids": rct_ids,
                  "product_ids": pro_ids}

        return result

    def extract_reaction_data(self, directory, opt=None, freq=None, sp=None):
        """
        Gathers all relevant reaction parameters, including references to
        each job performed.

        :param directory: Directory name where the reaction is stored. Right
            now, this is the easiest way to identify the reaction. In the
            future, more sophisticated searching should be used.
        :param opt: dict containing information about the optimization jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param freq: dict containing information about the frequency jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param sp: dict containing information about the single-point jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.

        :return: dict
        """

        if self.db is None:
            raise RuntimeError("Could not connect to database. Check db_file"
                               "and try again later.")

        # To extract enthalpy and entropy from calculation results
        def get_thermo(job):
            enthalpy = 0
            entropy = 0
            energy_opt = 0
            energy_sp = 0

            for calc in job["calcs_reversed"]:
                if calc["task"]["type"] == "opt" or calc["task"]["type"] == "optimization":
                    energy_opt = calc["final_energy"]
                if calc["task"]["type"] == "freq" or calc["task"]["type"] == "frequency":
                    enthalpy = calc["enthalpy"]
                    entropy = calc["entropy"]
                if calc["task"]["type"] == "sp":
                    energy_sp = calc["final_energy_sp"]

            if energy_sp == 0:
                return {"enthalpy": enthalpy,
                        "entropy": entropy}
            else:
                return {"enthalpy": (enthalpy - energy_opt) + energy_sp,
                        "entropy": entropy}

        if abspath(directory) != directory:
            directory = join(self.base_dir, directory)

        # This is horribly inefficient. Should change the how data is stored
        # to allow for nicer queries
        all_records = self.db.collection.find()
        records = []

        dir_ids = [extract_id(f) for f in listdir(directory) if
                   f.endswith(".mol")]

        for record in all_records:
            molecule_id = extract_id(record["task_label"])
            if record["dir_name"] == directory or molecule_id in dir_ids:
                records.append(record)
                # To guarantee that only one such record is copied
                dir_ids.remove(molecule_id)

        # Sort files for if they are reactants or products
        reactants = []
        products = []
        for record in records:
            filename = record["task_label"].split("/")[-1]
            if opt is None:
                for calc in record["calcs_reversed"]:
                    if calc["task"]["type"] == "opt" or \
                            calc["task"]["type"] == "optimization":
                        method = calc["input"]["rem"]["method"]
                        basis = calc["input"]["rem"]["basis"]
                        solvent_method  = calc["input"]["rem"].get(
                            "solvent_method", None)
                        if solvent_method == "smd" or solvent_method == "sm12":
                            if calc["input"]["smx"] is None:
                                solvent = None
                            else:
                                solvent = calc["input"]["smx"]["solvent"]
                        elif solvent_method == "pcm":
                            solvent = calc["input"]["solvent"]
                        else:
                            solvent = None

                        opt = {"method": method,
                               "basis": basis,
                               "solvent_method": solvent_method,
                               "solvent": solvent}
                        break
            if freq is None:
                for calc in record["calcs_reversed"]:
                    if calc["task"]["type"] == "freq" or \
                            calc["task"]["type"] == "frequency":
                        method = calc["input"]["rem"]["method"]
                        basis = calc["input"]["rem"]["basis"]
                        solvent_method  = calc["input"]["rem"].get(
                            "solvent_method", None)
                        if solvent_method == "smd" or solvent_method == "sm12":
                            if calc["input"]["smx"] is None:
                                solvent = None
                            else:
                                solvent = calc["input"]["smx"]["solvent"]
                        elif solvent_method == "pcm":
                            solvent = calc["input"]["solvent"]
                        else:
                            solvent = None

                        freq = {"method": method,
                               "basis": basis,
                               "solvent_method": solvent_method,
                               "solvent": solvent}
                        break
            if sp is None:
                for calc in record["calcs_reversed"]:
                    if calc["task"]["type"] == "sp":
                        method = calc["input"]["rem"]["method"]
                        basis = calc["input"]["rem"]["basis"]
                        solvent_method  = calc["input"]["rem"].get(
                            "solvent_method", None)
                        if solvent_method == "smd" or solvent_method == "sm12":
                            if calc["input"]["smx"] is None:
                                solvent = None
                            else:
                                solvent = calc["input"]["smx"]["solvent"]
                        elif solvent_method == "pcm":
                            solvent = calc["input"]["solvent"]
                        else:
                            solvent = None

                        sp = {"method": method,
                               "basis": basis,
                               "solvent_method": solvent_method,
                               "solvent": solvent}
                        break

            if filename.startswith(self.reactant_pre):
                reactants.append(record)
            elif filename.startswith(self.product_pre):
                products.append(record)
            else:
                print("Skipping {} because it cannot be determined if it is"
                      "reactant or product.".format(filename))
                continue

        # Get ids
        reactant_ids = [r["_id"] for r in reactants]
        product_ids = [p["_id"] for p in products]

        # Get thermo data
        rct_thermo = [get_thermo(r) for r in reactants]
        pro_thermo = [get_thermo(p) for p in products]

        # Compile reaction thermo from reactant and product thermos
        delta_h = sum(p["enthalpy"] for p in pro_thermo) - sum(r["enthalpy"] for r in rct_thermo)
        delta_h *= 1000 * 4.184
        delta_s = sum(p["entropy"] for p in pro_thermo) - sum(r["entropy"] for r in rct_thermo)
        delta_s *= 4.184
        thermo = {
            "enthalpy": delta_h,
            "entropy": delta_s
        }

        try:
            thermo["t_critical"] = delta_h / delta_s
        except ZeroDivisionError:
            thermo["t_critical"] = 0

        result = {"dir_name": directory,
                  "opt": opt,
                  "freq": freq,
                  "sp": sp,
                  "reactant_ids": reactant_ids,
                  "product_ids": product_ids,
                  "thermo": thermo}

        return result

    def record_reaction_data_db(self, directory, use_files=True, use_db=False,
                                opt=None, freq=None, sp=None):
        """
        Record thermo data in thermo collection.

        :param directory: Directory name where the reaction is stored. Right
            now, this is the easiest way to identify the reaction. In the
            future, more sophisticated searching should be used.
        :param use_files: If set to True (default True), use
            get_reaction_thermo_files to gather data
        :param use_db: If set to True (default False), use
            extract_reaction_data to gather data
        :param opt: dict containing information about the optimization jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param freq: dict containing information about the frequency jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param sp: dict containing information about the single-point jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.

        :return:
        """

        if self.db is None:
            raise RuntimeError("Could not connect to database. Check db_file"
                               "and try again later.")

        collection = self.db.db["thermo"]

        if use_db:
            collection.insert_one(self.extract_reaction_data(directory, opt=opt,
                                                         freq=freq, sp=sp))
        elif use_files:
            collection.insert_one(self.get_reaction_thermo_files(directory))
        else:
            raise RuntimeError("Either database or files must be used to "
                               "extract thermo data.")

    def record_reaction_data_file(self, directory, filename="thermo.txt",
                                  use_files=True, use_db=False, opt=None,
                                  freq=None, sp=None):
        """
        Record thermo data in thermo.txt file.

        Note: This function does NOT store the reactant and product IDs

        :param directory: Directory name where the reaction is stored. Right
            now, this is the easiest way to identify the reaction. In the
            future, more sophisticated searching should be used.
        :param filename: File (within directory) where data should be stored.
            By default, it will be stored in thermo.txt.
        :param use_files: If set to True (default True), use
            get_reaction_thermo_files to gather data
        :param use_db: If set to True (default False), use
            extract_reaction_data to gather data
        :param opt: dict containing information about the optimization jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param freq: dict containing information about the frequency jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param sp: dict containing information about the single-point jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.

        :return:
        """

        if abspath(directory) != directory:
            directory = join(self.base_dir, directory)

        with open(join(directory, filename), "w+") as file:
            if use_db:
                data = self.extract_reaction_data(directory, opt=opt, freq=freq,
                                                  sp=sp)
            elif use_files:
                data = self.get_reaction_thermo_files(directory)
            else:
                raise RuntimeError("Either database or files must be used to "
                                   "extract thermo data.")

            file.write("Directory: {}\n".format(data["dir_name"]))
            file.write("Optimization Input: {}\n".format(data["opt"]))
            file.write("Frequency Input: {}\n".format(data["freq"]))
            file.write("Single-Point Input: {}\n".format(data["sp"]))
            file.write("Reaction Enthalpy: {}\n".format(data["thermo"]["enthalpy"]))
            file.write("Reaction Entropy: {}\n".format(data["thermo"]["entropy"]))
            file.write("Critical/Switching Temperature: {}\n".format(data["thermo"]["t_critical"]))

    def copy_outputs_across_directories(self):
        """
        Copy output files between subdirectories to ensure that all reaction
        directories that need outputs of a given molecule will have them.

        Note: This function should not be used unless necessary. It was written
        because for each directory, only a single database entry was being made
        (because db entries were being overwritten by default.

        :return:
        """

        files_copied = 0

        dirs = [d for d in listdir(self.base_dir) if isdir(join(self.base_dir, d)) and not d.startswith("block")]
        print("Number of directories: {}".format(len(dirs)))

        for start_d in dirs:
            start_p = join(self.base_dir, start_d)
            mol_files = [f for f in listdir(start_p) if isfile(join(start_p, f)) and f.endswith(".mol")]
            out_files = [f for f in listdir(start_p) if isfile(join(start_p, f)) and ".out" in f]

            for mf in mol_files:
                is_covered = False
                mol_id = extract_id(mf)

                mol_obj = get_molecule(join(start_p, mf))

                for out in out_files:
                    qcout = QCOutput(join(start_p, out))
                    if sorted(qcout.data["initial_molecule"].species) == sorted(mol_obj.species):
                        # If there is already output, do not copy any files
                        is_covered = True

                if is_covered:
                    continue

                for other_d in dirs:
                    if other_d == start_d:
                        continue
                    if is_covered:
                        break

                    other_p = join(self.base_dir, other_d)
                    # Check if this id is present
                    other_mol_files = [f for f in listdir(other_p) if isfile(join(other_p, f)) and f.endswith(".mol") and mol_id in f]
                    other_out_files = [f for f in listdir(other_p) if isfile(join(other_p, f)) and ".out" in f]
                    to_copy = []
                    for other_mol in other_mol_files:
                        if other_mol.startswith(self.product_pre):
                            to_copy = [f for f in other_out_files if
                                       f.startswith(self.product_pre)]
                        elif other_mol.startswith(self.reactant_pre):
                            to_check = [f for f in other_out_files if f.startswith(self.reactant_pre)]
                            to_copy = []
                            for file in to_check:
                                qcout = QCOutput(join(other_p, file))
                                if qcout.data["initial_molecule"].species == mol_obj.species:
                                    to_copy.append(file)
                        else:
                            to_copy = []
                    for file in to_copy:
                        shutil.copyfile(join(other_p, file), join(start_p, file + "_copy"))
                        files_copied += 1

                    if files_copied > 0:
                        is_covered = True
        print("Number of files copied: {}".format(files_copied))

    def find_common_reactants(self, rct_id):
        """
        Searches all subdirectories for those that have reactant .mol files with
        unique id rct_id.

        :param rct_id: String representing unique identifier for Reaxys
            molecules.
        :return: List of reaction directories containing the given reactant.
        """
        results = []
        for d in listdir(self.base_dir):
            if isdir(join(self.base_dir, d)) and not d.startswith("block"):
                for f in listdir(join(self.base_dir, d)):
                    if rct_id in f:
                        results.append(d)
        return results

    def map_reactants_to_reactions(self):
        """
        Construct a dict showing which directories share each reactant.

        This is useful for analysis of common reactants, and to identify the
        "source" of a given reactant (in which directory the calculation
        actually took place).

        :return:
        """

        mapping = {}
        dirs = [d for d in listdir(self.base_dir)
                if isdir(join(self.base_dir, d)) and not d.startswith("block")]

        appropriate_dirs = self.quick_check(dirs)

        for d in appropriate_dirs:
            if isdir(join(self.base_dir, d)) and not d.startswith("block"):
                molfiles = [f for f in listdir(join(self.base_dir, d))
                            if f.endswith(".mol")
                            and f.startswith(self.reactant_pre)]
                for file in molfiles:
                    f_id = extract_id(file)
                    if f_id in mapping:
                        mapping[f_id].append(d)
                    else:
                        mapping[f_id] = [d]

        return mapping
